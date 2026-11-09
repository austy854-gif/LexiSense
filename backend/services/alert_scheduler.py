"""Production daily expiration/renewal scheduler (non-agentic)."""

from __future__ import annotations

import logging
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from services.alert_service import retry_failed_alerts, scan_expiration_alerts

logger = logging.getLogger(__name__)

RETRY_INTERVAL_MINUTES = 15


def _env_int(name: str, default: int, low: int, high: int) -> int:
    """Parse an optional int env var; a typo must never stop API startup."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default
    return max(low, min(high, value))


def build_alert_scheduler(db, is_ready=lambda: True) -> AsyncIOScheduler:
    """``is_ready`` gates every run: scans must not run before the bootstrap has
    created the unique alert idempotency index."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def run_scan():
        """Scan for expiring contracts only after alert storage is ready."""
        if not is_ready():
            logger.warning("Skipping alert scan: bootstrap or alert index not ready")
            return
        try:
            result = await scan_expiration_alerts(db)
            logger.info("Daily expiration alert scan complete", extra=result)
        except Exception:
            logger.exception("Daily expiration alert scan failed")

    async def run_retries():
        """Retry due alert deliveries only after alert storage is ready."""
        if not is_ready():
            return
        try:
            result = await retry_failed_alerts(db)
            if result.get("queued"):
                logger.info("Expiration alert retry pass complete", extra=result)
        except Exception:
            logger.exception("Expiration alert retry pass failed")

    hour = _env_int("ALERT_SCAN_UTC_HOUR", 8, 0, 23)
    minute = _env_int("ALERT_SCAN_UTC_MINUTE", 0, 0, 59)
    scheduler.add_job(
        run_scan,
        CronTrigger(hour=hour, minute=minute, timezone="UTC"),
        id="daily-expiration-renewal-alerts",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    # Failed deliveries are retried (bounded by MAX_ATTEMPTS, persisted on each
    # record) without waiting for the next daily scan.
    scheduler.add_job(
        run_retries,
        IntervalTrigger(minutes=RETRY_INTERVAL_MINUTES, timezone="UTC"),
        id="expiration-alert-retries",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler
