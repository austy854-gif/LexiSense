from fastapi import FastAPI, APIRouter, Request, Response
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import time
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Configure structured logging FIRST (before other imports that may log)
from utils.logging_config import setup_logging, RequestLoggingMiddleware, get_logger
setup_logging(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    json_format=os.environ.get("LOG_JSON", "true").lower() == "true"
)
logger = get_logger("main")

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
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

# Create the main app
app = FastAPI(
    title="LexiSense API",
    description="Enterprise AI-powered Contract Lifecycle Management",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

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

@api_router.get("/")
async def root():
    return {"message": "LexiSense API", "version": "2.0.0", "docs": "/api/docs"}

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint with detailed checks
@api_router.get("/health")
async def health_check():
    """Health check endpoint with detailed service status."""
    checks = {}
    overall_healthy = True
    
    # Database check
    try:
        await db.command("ping")
        checks["database"] = {"status": "healthy", "details": "Connected"}
    except Exception as e:
        checks["database"] = {"status": "unhealthy", "details": str(e)}
        overall_healthy = False
    
    # S3 check
    try:
        from services.storage_service import get_s3_client
        s3_client = get_s3_client()
        if s3_client:
            s3_client.head_bucket(Bucket=os.environ.get("AWS_S3_BUCKET", ""))
            checks["storage"] = {"status": "healthy", "details": "S3 accessible"}
        else:
            checks["storage"] = {"status": "degraded", "details": "Using mock storage"}
    except Exception as e:
        checks["storage"] = {"status": "unhealthy", "details": str(e)}
    
    # AI service check
    try:
        emergent_key = os.environ.get("EMERGENT_LLM_KEY")
        if emergent_key and not emergent_key.startswith("your-"):
            checks["ai"] = {"status": "healthy", "details": "Configured"}
        else:
            checks["ai"] = {"status": "degraded", "details": "Not configured"}
    except Exception as e:
        checks["ai"] = {"status": "unhealthy", "details": str(e)}
    
    # Email service check
    try:
        resend_key = os.environ.get("RESEND_API_KEY")
        if resend_key and not resend_key.startswith("your-"):
            checks["email"] = {"status": "healthy", "details": "Configured"}
        else:
            checks["email"] = {"status": "degraded", "details": "Not configured"}
    except Exception as e:
        checks["email"] = {"status": "unhealthy", "details": str(e)}
    
    return {
        "status": "healthy" if overall_healthy else "degraded",
        "version": "2.0.0",
        "checks": checks,
        "timestamp": time.time()
    }

@app.on_event("startup")
async def startup_event():
    logger.info("LexiSense API starting up...")

    # Create indexes for better query performance
    await db.users.create_index("email", unique=True)
    await db.users.create_index("organizationId")
    await db.contracts.create_index("organizationId")
    await db.contracts.create_index([("organizationId", 1), ("createdAt", -1)])
    await db.contracts.create_index([("organizationId", 1), ("expiryDate", 1)])
    await db.contracts.create_index([("organizationId", 1), ("riskLevel", 1)])
    await db.contracts.create_index([("organizationId", 1), ("contractType", 1)])
    await db.invitations.create_index("token", unique=True)
    await db.invitations.create_index([("organizationId", 1), ("email", 1)])
    await db.contract_versions.create_index([("contractId", 1), ("version", -1)])
    await db.expiration_alerts.create_index([("contractId", 1), ("daysBeforeExpiry", 1)])
    await db.templates.create_index([("organizationId", 1), ("name", 1)])
    await db.audit_logs.create_index([("organizationId", 1), ("createdAt", -1)])
    await db.audit_logs.create_index([("organizationId", 1), ("resourceType", 1)])
    await db.notifications.create_index([("userId", 1), ("createdAt", -1)])
    await db.notifications.create_index([("userId", 1), ("isRead", 1)])
    logger.info("Database indexes created")

    # Ensure S3 bucket exists
    from services.storage_service import ensure_bucket_exists
    bucket_ok = await ensure_bucket_exists()
    if bucket_ok:
        logger.info("S3 bucket verified")
    else:
        logger.warning("S3 bucket not available (check AWS credentials)")

    # Initialize scheduler for daily alert emails
    from services.scheduler_service import init_scheduler
    init_scheduler(db)
    logger.info("Scheduler initialized")

@app.on_event("shutdown")
async def shutdown_db_client():
    from services.scheduler_service import shutdown_scheduler
    shutdown_scheduler()
    client.close()
