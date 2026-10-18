"""Delivery of approval emails: Celery task with retries, plus an inline
fallback used when Celery is not configured. Only ids travel through the
queue; tokens are minted and the email rendered at send time (in the worker or
the inline fallback). Retries are idempotent: tokens are deterministic per
notification, an already-sent notification is skipped, and the Resend
idempotency key is the notification id. Failures are logged and recorded on
the ``email_notifications`` record; they never propagate to the API request."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from celery import shared_task

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
_ALREADY_SENT = {"sent", "logged", "delivered", "delivery_delayed", "bounced", "complained"}


class ApprovalEmailDeliveryError(Exception):
    """Sanitised retry reason: carries only the original exception type name."""


def _http_status(exc: BaseException) -> Optional[int]:
    """The provider's HTTP status code, if the exception carries one.

    ``resend.exceptions.ResendError`` exposes it as ``code``; HTTP-client
    errors as ``status_code`` or ``response.status_code``. Only a plausible
    integer status (100-599) is returned -- never free text.
    """
    candidates = (
        getattr(exc, "code", None),
        getattr(exc, "status_code", None),
        getattr(exc, "http_status", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    )
    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def safe_error_summary(exc: BaseException) -> str:
    """``'ResendError (HTTP 422)'`` / ``'TimeoutError'``: type plus status only.

    Stored as ``email_notifications.lastError``. Provider error text is never
    kept: it can echo the recipient address or the rendered links (tokens).
    """
    try:
        status = _http_status(exc)
    except Exception:
        status = None
    name = type(exc).__name__
    return f"{name} (HTTP {status})" if status is not None else name


def _recipient_domain(address) -> str:
    from services.email_service import _recipient_domain as domain_of
    return domain_of(address)


def _db(db=None):
    if db is not None:
        return db
    from services.task_db import current_db
    return current_db.get()


async def _render(db, notification: dict) -> Optional[Tuple[str, str]]:
    """(subject, html) for the notification, or None if it no longer applies."""
    from services import approval_service

    approval = await db.approvals.find_one(
        {"id": notification["approvalId"], "organizationId": notification["organizationId"]}, {"_id": 0}
    )
    if not approval:
        return None
    if notification["kind"] == "approval_request":
        if approval.get("status") != "pending":
            return None
        tokens = await approval_service.mint_tokens_for_notification(db, approval, notification["id"])
        return approval_service.render_request_email(approval, None, tokens)
    if notification["kind"] == "approval_decision":
        return approval_service.render_decision_email(approval)
    return None


async def deliver_notification(db, notification_id: str, organization_id: str, final_attempt: bool = True) -> dict:
    """Render and send one notification, recording the outcome. Raises on a
    provider error so the Celery task can retry; marks ``failed`` on the final attempt."""
    from services import email_service

    now = datetime.now(timezone.utc).isoformat()
    query = {"id": notification_id, "organizationId": organization_id}
    notification = await db.email_notifications.find_one(query, {"_id": 0})
    if not notification:
        return {"status": "missing"}
    if notification.get("status") in _ALREADY_SENT:
        return {"status": "duplicate_skipped"}
    rendered = await _render(db, notification) if notification.get("to") else None
    if rendered is None:
        await db.email_notifications.update_one(query, {"$set": {"status": "skipped", "updatedAt": now}})
        return {"status": "skipped"}
    subject, html = rendered
    try:
        result = await email_service.send_raw_email(
            notification["to"], subject, html,
            tags=[{"name": "notification_id", "value": notification_id}],
            idempotency_key=f"lexisense-notification-{notification_id}",
        )
    except Exception as exc:
        # Metadata only: provider error text can echo the recipient or links.
        logger.warning(
            "Approval email send failed (template=%s recipient_domain=%s error_type=%s)",
            notification.get("kind"), _recipient_domain(notification.get("to")), type(exc).__name__,
        )
        await db.email_notifications.update_one(
            query, {"$inc": {"attempts": 1},
                    "$set": {"status": "failed" if final_attempt else "retrying",
                             "lastError": safe_error_summary(exc), "updatedAt": now}})
        raise
    await db.email_notifications.update_one(
        query, {"$inc": {"attempts": 1},
                "$set": {"status": result.get("status", "sent"), "resendEmailId": result.get("email_id"),
                         "sentAt": now, "updatedAt": now, "lastError": None}})
    return result


@shared_task(bind=True, max_retries=MAX_RETRIES, default_retry_delay=30, acks_late=True)
def send_approval_email_task(self, notification_id: str, organization_id: str):
    db = _db()
    if db is None:
        logger.error("Database not initialised for approval email %s", notification_id)
        return {"status": "error"}
    final = self.request.retries >= MAX_RETRIES
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(deliver_notification(db, notification_id, organization_id, final_attempt=final))
    except Exception as exc:
        logger.warning("Approval email %s failed (attempt %s, error_type=%s)",
                       notification_id, self.request.retries + 1, type(exc).__name__)
        if final:
            return {"status": "failed"}
        # Celery logs the retry reason; hand it a sanitised exception so the
        # provider's error text never reaches worker logs.
        raise self.retry(exc=ApprovalEmailDeliveryError(type(exc).__name__),
                         countdown=min(600, 30 * 2 ** self.request.retries))
    finally:
        loop.close()


async def dispatch_notification(db, notification_id: str, organization_id: str) -> str:
    """Queue on Celery when a broker is configured, else send inline. Never raises."""
    try:
        from services.celery_app import CELERY_BROKER_URL
    except Exception:  # pragma: no cover - celery import problems
        CELERY_BROKER_URL = None
    if CELERY_BROKER_URL:
        try:
            from fastapi.concurrency import run_in_threadpool
            await run_in_threadpool(send_approval_email_task.apply_async,
                                    args=(notification_id, organization_id), retry=False)
            return "queued"
        except Exception:
            logger.exception("Could not enqueue approval email %s; sending inline", notification_id)
    try:
        result = await deliver_notification(db, notification_id, organization_id)
        return result.get("status", "sent")
    except Exception as exc:
        # No traceback/exception text: it can carry provider-echoed recipients
        # or links. deliver_notification already logged template + domain.
        logger.error("Approval email %s failed (inline send, error_type=%s)", notification_id, type(exc).__name__)
        return "failed"
