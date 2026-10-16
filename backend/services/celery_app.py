"""
Celery application for LexiSense Agentic AI Platform.
Distributed task queue for background AI processing.
"""
import logging
import os
import ssl
from celery import Celery, Task
from celery.signals import task_prerun, task_postrun
from celery.schedules import crontab
from kombu import Queue

# Configuration
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND") or os.environ.get("REDIS_URL")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature-flag gating (see utils/feature_flags.py; every flag defaults to OFF).
# Only task modules and beat jobs for enabled groups are registered, so a
# worker started with -B runs NO agentic beat jobs unless a flag is turned on.
# ---------------------------------------------------------------------------
from utils.feature_flags import enabled_groups  # noqa: E402

#: feature group -> task module
GROUP_TASK_MODULES = {
    "risk": "services.risk_tasks",
    "obligations": "services.obligation_tasks",
    "agents": "services.agent_tasks",
    "intake": "services.intake_tasks",
    "redline": "services.playbook_tasks",
}

#: beat job name -> feature group that must be enabled for it to be scheduled.
#: weekly-cleanup archives completed obligations (>1y) and deletes acknowledged
#: obligation alerts (>90d); it is retention housekeeping for the obligations
#: feature, so it rides on the obligations flag.
BEAT_JOB_GROUPS = {
    "check-due-agents": "agents",
    "daily-risk-refresh": "risk",
    "daily-obligation-alerts": "obligations",
    "retry-failed-intakes": "intake",
    "weekly-cleanup": "obligations",
}

ENABLED_GROUPS = enabled_groups()
TASK_MODULES = tuple(m for g, m in GROUP_TASK_MODULES.items() if g in ENABLED_GROUPS)

# Approval emails are not an agentic group; they have their own flag.
from utils.feature_flags import approval_emails_enabled  # noqa: E402

if approval_emails_enabled():
    TASK_MODULES = TASK_MODULES + ("services.approval_tasks",)

_ALL_BEAT_JOBS = {
    # Contract agents - check every hour for due agents
    "check-due-agents": {
        "task": "services.agent_tasks.check_due_agents",
        "schedule": crontab(minute=0),  # Every hour at minute 0
    },
    # Daily risk assessment refresh for active contracts
    "daily-risk-refresh": {
        "task": "services.risk_tasks.refresh_stale_risk_assessments",
        "schedule": crontab(hour=3, minute=0),  # 3 AM daily
    },
    # Daily obligation alert check
    "daily-obligation-alerts": {
        "task": "services.obligation_tasks.send_daily_obligation_alerts",
        "schedule": crontab(hour=8, minute=0),  # 8 AM daily
    },
    # Retry failed intakes every 30 minutes
    "retry-failed-intakes": {
        "task": "services.intake_tasks.retry_failed_intakes",
        "schedule": crontab(minute="*/30"),  # Every 30 minutes
    },
    # Weekly cleanup
    "weekly-cleanup": {
        "task": "services.obligation_tasks.cleanup_completed_obligations",
        "schedule": crontab(hour=2, minute=0, day_of_week=0),  # Sunday 2 AM
    },
}

BEAT_SCHEDULE = {
    name: job for name, job in _ALL_BEAT_JOBS.items() if BEAT_JOB_GROUPS.get(name) in ENABLED_GROUPS
}

if not CELERY_BROKER_URL:
    logger.warning(
        "CELERY_BROKER_URL/REDIS_URL not set - background tasks are disabled; "
        "calling .delay()/.apply_async() will raise CeleryNotConfiguredError"
    )


class CeleryNotConfiguredError(RuntimeError):
    """Raised when a task is enqueued but no broker is configured."""


class GuardedTask(Task):
    """Fail fast with a clear error instead of silently connecting to a
    default broker when CELERY_BROKER_URL/REDIS_URL is unset."""

    def apply_async(self, *args, **kwargs):
        if not CELERY_BROKER_URL:
            raise CeleryNotConfiguredError(
                f"Cannot enqueue task {self.name!r}: set CELERY_BROKER_URL or REDIS_URL"
            )
        return super().apply_async(*args, **kwargs)


# Create Celery app
celery_app = Celery(
    "lexisense_agentic",
    task_cls=GuardedTask,
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=list(TASK_MODULES),
)

# Celery configuration
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    
    # Timezone
    timezone="UTC",
    enable_utc=True,
    
    # Task execution
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes hard limit
    task_soft_time_limit=25 * 60,  # 25 minutes soft limit
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    
    # Worker
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    worker_disable_rate_limits=False,
    
    # Results
    result_expires=3600,  # 1 hour
    result_extended=True,
    
    # Beat schedule (periodic tasks)
    
    # Beat schedule (periodic tasks) -- filtered by feature flags above.
    beat_schedule=BEAT_SCHEDULE,

    # Task routing
    task_routes={
        "services.agent_tasks.*": {"queue": "scheduled"},
        "services.risk_tasks.*": {"queue": "high_priority"},
        "services.playbook_tasks.*": {"queue": "default"},
        "services.obligation_tasks.*": {"queue": "default"},
        "services.intake_tasks.*": {"queue": "high_priority"},
        "services.approval_tasks.*": {"queue": "high_priority"},
    },
    
    # Queue definitions
    task_default_queue="default",
    task_queues=(
        Queue("high_priority", routing_key="high_priority"),
        Queue("default", routing_key="default"),
        Queue("scheduled", routing_key="scheduled"),
        Queue("low_priority", routing_key="low_priority"),
    ),
    
    # Broker connection timeouts: fail fast instead of hanging an API request
    # (or the worker) when Redis is unreachable.
    broker_connection_timeout=float(os.environ.get("CELERY_BROKER_CONNECTION_TIMEOUT", "3")),
    broker_transport_options={
        "socket_timeout": float(os.environ.get("CELERY_BROKER_SOCKET_TIMEOUT", "5")),
        "socket_connect_timeout": float(os.environ.get("CELERY_BROKER_CONNECT_TIMEOUT", "3")),
        # Seconds an idle worker blocks per BRPOP before re-polling. The kombu
        # default (1s) costs ~86k Redis commands/day per queue on metered
        # Redis such as Upstash; 10s cuts that ~10x. Requires kombu>=5.6.
        "polling_interval": 10,
    },
    redis_socket_timeout=float(os.environ.get("CELERY_BROKER_SOCKET_TIMEOUT", "5")),
    redis_socket_connect_timeout=float(os.environ.get("CELERY_BROKER_CONNECT_TIMEOUT", "3")),

    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,
    
    # Retry policy
    task_autoretry_for=(Exception,),
    task_retry_backoff=True,
    task_retry_backoff_max=600,
    task_retry_jitter=True,
)

# Managed Redis (e.g. Upstash) uses rediss:// URLs. Celery refuses those
# unless ssl_cert_reqs is set, so default to verified TLS when the URL does
# not carry the parameter itself.
_REDIS_SSL = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
if CELERY_BROKER_URL and CELERY_BROKER_URL.startswith("rediss://") and "ssl_cert_reqs" not in CELERY_BROKER_URL:
    celery_app.conf.broker_use_ssl = _REDIS_SSL
if CELERY_RESULT_BACKEND and CELERY_RESULT_BACKEND.startswith("rediss://") and "ssl_cert_reqs" not in CELERY_RESULT_BACKEND:
    celery_app.conf.redis_backend_use_ssl = _REDIS_SSL


# ---------------------------------------------------------------------------
# Worker-side database wiring. Each task gets a short-lived Motor client (a
# Motor client binds to the event loop it is first used on, and every task runs
# its own loop). The handle lives in services.task_db.current_db, a ContextVar
# set here and reset on postrun, so it is safe under any pool (prefork,
# threads, solo) -- unlike the old module global, which threads would share.
# ---------------------------------------------------------------------------
_task_mongo_clients = {}


def _db_name_from(url: str) -> str:
    name = os.environ.get("DB_NAME")
    if name:
        return name
    try:
        from pymongo.uri_parser import parse_uri
        return parse_uri(url).get("database") or "lexisense"
    except Exception:
        return "lexisense"


@task_prerun.connect
def _wire_task_database(task_id=None, **_):
    url = os.environ.get("DATABASE_URL") or os.environ.get("MONGO_URL")
    if not url:
        logger.error("DATABASE_URL/MONGO_URL not set - tasks cannot reach the database")
        return
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.task_db import current_db

    client = AsyncIOMotorClient(url, connect=False, serverSelectionTimeoutMS=5_000, appname="lexisense-worker")
    token = current_db.set(client[_db_name_from(url)])
    _task_mongo_clients[task_id] = (client, token)


@task_postrun.connect
def _close_task_database(task_id=None, **_):
    entry = _task_mongo_clients.pop(task_id, None)
    if entry is not None:
        client, token = entry
        from services.task_db import current_db

        try:
            current_db.reset(token)
        except ValueError:  # different context (should not happen); just clear it
            current_db.set(None)
        client.close()


# Auto-discover tasks
celery_app.autodiscover_tasks(list(TASK_MODULES))


@celery_app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug task for testing Celery connectivity."""
    print(f"Request: {self.request!r}")


def init_celery():
    """Initialize Celery app. Call on FastAPI startup."""
    return celery_app


def shutdown_celery():
    """Shutdown Celery app. Call on FastAPI shutdown."""
    celery_app.close()