"""
Celery application for LexiSense Agentic AI Platform.
Distributed task queue for background AI processing.
"""
import os
from celery import Celery
from celery.schedules import crontab
from kombu import Queue

# Configuration
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# Create Celery app
celery_app = Celery(
    "lexisense_agentic",
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