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
    include=[
        "services.agent_tasks",
        "services.risk_tasks",
        "services.playbook_tasks",
        "services.obligation_tasks",
        "services.intake_tasks",
    ]
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
    beat_schedule={
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
    },
    
    # Task routing
    task_routes={
        "services.agent_tasks.*": {"queue": "scheduled"},
        "services.risk_tasks.*": {"queue": "high_priority"},
        "services.playbook_tasks.*": {"queue": "default"},
        "services.obligation_tasks.*": {"queue": "default"},
        "services.intake_tasks.*": {"queue": "high_priority"},
    },
    
    # Queue definitions
    task_default_queue="default",
    task_queues=(
        Queue("high_priority", routing_key="high_priority"),
        Queue("default", routing_key="default"),
        Queue("scheduled", routing_key="scheduled"),
        Queue("low_priority", routing_key="low_priority"),
    ),
    
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
# Worker-side database wiring. The task modules keep a module-level ``_db``
# that was only ever set by the (stripped) API router, so in a standalone
# worker every task returned "Database not initialized". Each task runs its
# own fresh asyncio loop, and a Motor client binds to the loop it is first
# used on, so a short-lived client is created per task and closed afterwards.
# ---------------------------------------------------------------------------
_TASK_MODULES = (
    "services.agent_tasks",
    "services.risk_tasks",
    "services.playbook_tasks",
    "services.obligation_tasks",
    "services.intake_tasks",
)
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
    import importlib
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(url, connect=False, serverSelectionTimeoutMS=5_000, appname="lexisense-worker")
    db = client[_db_name_from(url)]
    for mod in _TASK_MODULES:
        importlib.import_module(mod).set_database(db)
    _task_mongo_clients[task_id] = client


@task_postrun.connect
def _close_task_database(task_id=None, **_):
    client = _task_mongo_clients.pop(task_id, None)
    if client is not None:
        client.close()


# Auto-discover tasks
celery_app.autodiscover_tasks([
    "services.agent_tasks",
    "services.risk_tasks", 
    "services.playbook_tasks",
    "services.obligation_tasks",
    "services.intake_tasks",
])


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