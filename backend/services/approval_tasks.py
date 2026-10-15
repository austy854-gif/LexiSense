"""Delivery of approval emails: Celery task with retries, plus an inline
fallback used when Celery is not configured (matching the agentic pattern of
"no broker -> do not enqueue"). Failures are logged and recorded on the
``email_notifications`` record; they never propagate to the API request."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from celery import shared_task

logger = logging.getLogger(__name__)

MAX_RETRIES = 5


def _db(db=None):
    if db is not None:
        return db
    from services.task_db import current_db
    return current_db.get()


async def deliver_email(db, notification_id: str, organization_id: str, to_email: str, subject: str,
                        html: str, final_attempt: bool = True) -> dict:
    """Send one email and record the outcome. Raises on provider error so the
    Celery task can retry; marks the record ``failed`` on the final attempt."""
    from services import email_service

    now = datetime.now(timezone.utc).isoformat()
    query = {"id": notification_id, "organizationId": organization_id}
    try:
        result = await email_service.send_raw_email(
            to_email, subject, html, tags=[{"name": "notification_id", "value": notification_id}]
        )
    except Exception as exc:
        await db.email_notifications.update_one(
            query, {"$inc": {"attempts": 1},
                    "$set": {"status": "failed" if final_attempt else "retrying",
                             "lastError": str(exc)[:500], "updatedAt": now}})
        raise
    await db.email_notifications.update_one(
        query, {"$inc": {"attempts": 1},
                "$set": {"status": result.get("status", "sent"), "resendEmailId": result.get("email_id"),
                         "sentAt": now, "updatedAt": now, "lastError": None}})
    return result


@shared_task(bind=True, max_retries=MAX_RETRIES, default_retry_delay=30, acks_late=True)
def send_approval_email_task(self, notification_id: str, organization_id: str, to_email: str,
                             subject: str, html: str):
    db = _db()
    if db is None:
        logger.error("Database not initialised for approval email %s", notification_id)
        return {"status": "error"}
    final = self.request.retries >= MAX_RETRIES
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            deliver_email(db, notification_id, organization_id, to_email, subject, html, final_attempt=final)
        )
    except Exception as exc:
        logger.warning("Approval email %s failed (attempt %s): %s", notification_id, self.request.retries + 1, exc)
        if final:
            return {"status": "failed"}
        raise self.retry(exc=exc, countdown=min(600, 30 * 2 ** self.request.retries))
    finally:
        loop.close()


async def dispatch_email(db, notification_id: str, organization_id: str, to_email: Optional[str],
                         subject: str, html: str) -> str:
    """Queue on Celery when a broker is configured, else send inline. Never
    raises. Returns ``queued`` / ``sent`` / ``logged`` / ``failed`` / ``skipped``."""
    if not to_email:
        logger.warning("Approval email %s has no recipient; skipped", notification_id)
        return "skipped"
    try:
        from services.celery_app import CELERY_BROKER_URL
    except Exception:  # pragma: no cover - celery import problems
        CELERY_BROKER_URL = None
    if CELERY_BROKER_URL:
        try:
            from fastapi.concurrency import run_in_threadpool
            await run_in_threadpool(send_approval_email_task.apply_async,
                                    args=(notification_id, organization_id, to_email, subject, html), retry=False)
            return "queued"
        except Exception:
            logger.exception("Could not enqueue approval email %s; sending inline", notification_id)
    try:
        result = await deliver_email(db, notification_id, organization_id, to_email, subject, html)
        return result.get("status", "sent")
    except Exception:
        logger.exception("Approval email %s failed (inline send)", notification_id)
        return "failed"
