"""
Distributed task scheduler using Celery Beat + Redis.
Replaces in-memory APScheduler for production deployments.
"""
import os
import logging
from celery import Celery
from celery.schedules import crontab
from kombu import Queue

logger = logging.getLogger(__name__)

# Celery configuration
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# Create Celery app
celery_app = Celery(
    "lexisense",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=[
        "services.scheduler_tasks",
    ]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    result_expires=3600,
    beat_schedule={
        # Daily alert check at 9:00 AM UTC
        "daily-alert-check": {
            "task": "services.scheduler_tasks.check_and_send_daily_alerts",
            "schedule": crontab(hour=9, minute=0),
        },
        # Weekly cleanup of old audit logs (keep 90 days)
        "weekly-audit-cleanup": {
            "task": "services.scheduler_tasks.cleanup_old_audit_logs",
            "schedule": crontab(hour=2, minute=0, day_of_week=0),  # Sunday 2 AM
        },
        # Daily database index optimization
        "daily-index-optimization": {
            "task": "services.scheduler_tasks.optimize_database_indexes",
            "schedule": crontab(hour=3, minute=0),
        },
    },
    task_routes={
        "services.scheduler_tasks.*": {"queue": "scheduled"},
    },
    task_default_queue="default",
    task_queues=(
        Queue("default"),
        Queue("scheduled"),
        Queue("high_priority"),
    ),
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["services"])


def init_celery():
    """Initialize Celery app. Call this on startup."""
    logger.info(f"Celery initialized with broker: {CELERY_BROKER_URL}")
    return celery_app


def shutdown_celery():
    """Shutdown Celery app. Call this on shutdown."""
    logger.info("Celery shutdown")
    celery_app.close()


# For backwards compatibility - can be called from FastAPI startup/shutdown
def init_scheduler(database):
    """Initialize the distributed scheduler (Celery Beat runs separately)."""
    logger.info("Distributed scheduler (Celery Beat) should be running separately")
    logger.info(f"Broker: {CELERY_BROKER_URL}")
    # Store db reference for tasks if needed
    from services.scheduler_tasks import set_database
    set_database(database)


def shutdown_scheduler():
    """Shutdown the scheduler."""
    logger.info("Scheduler shutdown (Celery Beat runs separately)")