"""Production daily expiration/renewal scheduler (non-agentic)."""
from __future__ import annotations

import logging
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from services.alert_service import scan_expiration_alerts

logger = logging.getLogger(__name__)


def build_alert_scheduler(db) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def run_scan():
        try:
            result = await scan_expiration_alerts(db)
            logger.info("Daily expiration alert scan complete", extra=result)
        except Exception:
            logger.exception("Daily expiration alert scan failed")

    hour = max(0, min(23, int(os.environ.get("ALERT_SCAN_UTC_HOUR", "8"))))
    minute = max(0, min(59, int(os.environ.get("ALERT_SCAN_UTC_MINUTE", "0"))))
    scheduler.add_job(
        run_scan,
        CronTrigger(hour=hour, minute=minute, timezone="UTC"),
        id="daily-expiration-renewal-alerts",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    return scheduler
