from fastapi import FastAPI, APIRouter, Request, Response
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
import asyncio
import os
import logging
import re
import time
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Configure structured logging FIRST (before other imports that may log)
from utils.logging_config import (
    RequestLoggingMiddleware,
    get_logger,
    request_id_var,
    setup_logging,
)
setup_logging(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    json_format=os.environ.get("LOG_JSON", "true").lower() == "true"
)
logger = get_logger("main")

# MongoDB connection.
#
# Pool and timeout options are set explicitly instead of relying on driver
# defaults because every worker holds its own pool:
#   * maxPoolSize=50 caps worst-case connections at (workers x 50); with the
#     default of 100 a handful of replicas can exhaust a standard Atlas tier.
#   * maxIdleTimeMS retires sockets before an idle-timeout proxy/LB closes them,
#     which avoids periodic "connection reset" spikes.
#   * connect=False keeps import side-effect free: `motor` connects lazily, so a
#     transient database outage at boot no longer crashes the process before it
#     can serve health checks.
#   * serverSelectionTimeoutMS bounds how long a request waits for a primary,
#     so a database outage fails fast instead of hanging every request.
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(
    mongo_url,
    connect=False,
    maxPoolSize=int(os.environ.get("MONGO_MAX_POOL_SIZE", "50")),
    minPoolSize=0,
    maxIdleTimeMS=45_000,
    connectTimeoutMS=10_000,
    serverSelectionTimeoutMS=5_000,
    socketTimeoutMS=45_000,
    appname="lexisense-api",
)
db = client[os.environ['DB_NAME']]

# Initialize Sentry
sentry_dsn = os.environ.get("SENTRY_DSN")
if sentry_dsn and not sentry_dsn.startswith("your-"):
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_logging = LoggingIntegration(
        level=logging.INFO,
        event_level=logging.ERROR
    )

    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
            sentry_logging,
        ],
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment=os.environ.get("ENVIRONMENT", "development"),
        release=os.environ.get("RELEASE_VERSION", "2.0.0"),
    )
    logger.info("Sentry initialized", extra={"environment": os.environ.get("ENVIRONMENT", "development")})
else:
    logger.info("Sentry not configured (SENTRY_DSN not set)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle.

    Replaces the deprecated ``@app.on_event`` handlers so startup/shutdown work
    participates in the lifespan protocol (and is exercised by the test client
    instead of being skipped).
    """
    logger.info("LexiSense API starting up...")

    # Canonical index specification (see utils/database.py for the rationale
    # behind each index). Without the id indexes every by-id endpoint was a
    # collection scan, and the compound indexes back the list/dashboard sorts.
    from utils.database import apply_indexes
    result = await apply_indexes(db)
    logger.info(
        "Database indexes ensured",
        extra={"indexes_created": result["created"], "indexes_failed": result["failed"]},
    )

    # Ensure S3 bucket exists
    from services.storage_service import ensure_bucket_exists
    bucket_ok = await ensure_bucket_exists()
    if bucket_ok:
        logger.info("S3 bucket verified")
    else:
        logger.warning("S3 bucket not available (check AWS credentials)")

    # Celery is not initialized -- agentic subsystem is stripped for launch.
    # Re-enable when Redis + Celery worker + beat are provisioned.

    # Run pending database migrations (idempotent)
    try:
        from utils.migrations import run_migrations
        await run_migrations(db)
    except Exception as e:
        logger.error(f"Migration run failed: {e}")

    yield

    logger.info("LexiSense API shutting down...")
    client.close()


# Create the main app
app = FastAPI(
    title="LexiSense API",
    description="Enterprise AI-powered Contract Lifecycle Management",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Expose the handles so the lifespan protocol / tests can re-point them.
app.state.mongo_client = client
app.state.db_name = os.environ["DB_NAME"]

# Add request logging middleware
app.add_middleware(RequestLoggingMiddleware)

# Create a router with the /api/v1 prefix
api_router = APIRouter(prefix="/api/v1")

# Import and initialize routes
from routes.auth import router as auth_router, init_db as init_auth_db
from routes.contracts import router as contracts_router, init_db as init_contracts_db
from routes.team import router as team_router, init_db as init_team_db
from routes.dashboard import router as dashboard_router, init_db as init_dashboard_db
from routes.alerts import router as alerts_router, init_db as init_alerts_db
from routes.templates import router as templates_router, init_db as init_templates_db
from routes.export import router as export_router, init_db as init_export_db
from routes.analytics import router as analytics_router, init_db as init_analytics_db
from routes.audit import router as audit_router, init_db as init_audit_db
from routes.notifications import router as notifications_router, init_db as init_notifications_db
from routes.workflow import router as workflow_router, init_db as init_workflow_db
# NOTE: Agentic subsystem is stripped from launch (see PRODUCT_SCOPE.md).
# Backend code kept for future re-enable, but router is NOT mounted and DB is NOT initialized here.
# Also requires Redis + Celery worker + beat which are not provisioned for launch.
# from routes.agentic import router as agentic_router, init_db as init_agentic_db
from routes.billing import router as billing_router, init_db as init_billing_db
from services.audit_service import init_db as init_audit_service_db

# Initialize database for all route modules
init_auth_db(db)
init_contracts_db(db)
init_team_db(db)
init_dashboard_db(db)
init_alerts_db(db)
init_templates_db(db)
init_export_db(db)
init_analytics_db(db)
init_audit_db(db)
init_notifications_db(db)
init_workflow_db(db)
# init_agentic_db(db)  # Stripped for launch
init_billing_db(db)
init_audit_service_db(db)

# Include all routers
api_router.include_router(auth_router)
api_router.include_router(contracts_router)
api_router.include_router(team_router)
api_router.include_router(dashboard_router)
api_router.include_router(alerts_router)
api_router.include_router(templates_router)
api_router.include_router(export_router)
api_router.include_router(analytics_router)
api_router.include_router(audit_router)
api_router.include_router(notifications_router)
api_router.include_router(workflow_router)
# api_router.include_router(agentic_router)  # Stripped for launch
api_router.include_router(billing_router)

@api_router.get("/")
async def root():
    return {"message": "LexiSense API", "version": "2.0.0", "docs": "/api/docs"}


# Timeout budget for a single dependency probe inside the health check.
HEALTH_PROBE_TIMEOUT_SECONDS = float(os.environ.get("HEALTH_PROBE_TIMEOUT_SECONDS", "3"))


async def _probe_storage() -> dict:
    """Probe S3 on a worker thread inside a bounded timeout.

    ``boto3`` is synchronous: calling it directly from the event loop blocked
    all concurrent requests for the duration of the network round trip, and an
    unresponsive S3 endpoint could hang the health check indefinitely.
    """
    try:
        from services.storage_service import get_s3_client

        s3_client = get_s3_client()
        if not s3_client:
            return {"status": "degraded", "details": "Using mock storage"}

        await asyncio.wait_for(
            asyncio.to_thread(
                s3_client.head_bucket, Bucket=os.environ.get("AWS_S3_BUCKET", "")
            ),
            timeout=HEALTH_PROBE_TIMEOUT_SECONDS,
        )
        return {"status": "healthy", "details": "S3 accessible"}
    except asyncio.TimeoutError:
        return {"status": "unhealthy", "details": "Storage probe timed out"}
    except Exception as exc:
        logger.warning("Health check storage probe failed: %s", exc)
        return {"status": "unhealthy", "details": "Storage probe failed"}


def _probe_config(env_var: str) -> dict:
    """Report whether an optional integration is configured."""
    value = os.environ.get(env_var)
    if value and not value.startswith("your-"):
        return {"status": "healthy", "details": "Configured"}
    return {"status": "degraded", "details": "Not configured"}


# Health check endpoint with detailed checks. MUST be registered on
# `api_router` BEFORE `app.include_router(api_router)` -- otherwise the
# route is silently dropped (this is exactly the Gate 2 release-stopper
# from the pre-launch checklist).
@api_router.get("/health")
async def health_check():
    """Health check endpoint with detailed service status.

    Dependency failures are reported as a *status*, never as raw exception
    text, and the underlying error goes to the logs instead: this endpoint is
    unauthenticated, so echoing driver/proxy error strings leaks internal
    topology and credentials-adjacent detail to anonymous callers.
    """
    checks = {}
    overall_healthy = True

    # Database check
    try:
        await asyncio.wait_for(db.command("ping"), timeout=HEALTH_PROBE_TIMEOUT_SECONDS)
        checks["database"] = {"status": "healthy", "details": "Connected"}
    except asyncio.TimeoutError:
        checks["database"] = {"status": "unhealthy", "details": "Database probe timed out"}
        overall_healthy = False
    except Exception as exc:
        logger.warning("Health check database probe failed: %s", exc)
        checks["database"] = {"status": "unhealthy", "details": "Database unreachable"}
        overall_healthy = False

    # S3 check (offloaded -- see _probe_storage)
    checks["storage"] = await _probe_storage()

    # AI + email configuration checks (no outbound calls, no secret values)
    checks["ai"] = _probe_config("EMERGENT_LLM_KEY")
    checks["email"] = _probe_config("RESEND_API_KEY")

    return {
        "status": "healthy" if overall_healthy else "degraded",
        "version": "2.0.0",
        "checks": checks,
        "timestamp": time.time(),
    }


# Include the router in the main app
app.include_router(api_router)

# Parse CORS origins defensively: an unset/empty CORS_ORIGINS previously
# produced [''] (a bogus origin) instead of an empty allow-list.
cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if not cors_origins:
    logger.warning("CORS_ORIGINS is empty - no cross-origin requests will be allowed")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Total-Count", "X-Page-Limit", "X-Page-Offset"],
)

# ---------------------------------------------------------------------------
# Correlation id propagation + uniform error envelope
# ---------------------------------------------------------------------------
_ALLOWED_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Honour an inbound ``X-Request-ID`` (or mint one) and echo it back.

    Without this the correlation id existed only inside log lines, so a client
    reporting a failure had nothing to quote and support could not tie a
    response back to its trace.
    """

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _ALLOWED_REQUEST_ID.match(incoming) else str(uuid.uuid4())
        request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            # Reset so an id can never leak into a later request's context.
            request_id_var.set("")
        response.headers["X-Request-ID"] = request_id
        return response


app.add_middleware(RequestIDMiddleware)

# Every error -- HTTPException, validation failure or unhandled exception --
# returns one machine-readable envelope: {"error": {...}, "requestId", "status"}.
from utils.errors import register_exception_handlers  # noqa: E402
register_exception_handlers(app)
