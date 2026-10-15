"""Public endpoints behind the approval email links, and the Resend
delivery-status webhook. Mounted only when APPROVAL_EMAILS_ENABLED is on.

The email-action endpoints are unauthenticated by design: the signed,
single-use, expiring token *is* the credential, bound to one approval, one
approver, one tenant and one action. GET only previews (link scanners and
mail-client prefetchers follow GETs); POST performs the action.
"""
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services import approval_service
from services.approval_service import ApprovalTokenError
from services.approval_tasks import dispatch_email
from utils import svix
from utils.rbac import SlidingWindowRateLimiter, client_key, rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Approvals"])

db = None

#: Per-IP budget for the public token endpoints (token guessing is infeasible,
#: this just caps abuse).
APPROVAL_LINK_LIMITER = SlidingWindowRateLimiter(max_requests=30, window_seconds=60.0)
_link_rate_limit = rate_limit(APPROVAL_LINK_LIMITER, "approval_link")

#: Resend event type -> notification status
RESEND_EVENT_STATUS = {
    "email.sent": "sent",
    "email.delivered": "delivered",
    "email.delivery_delayed": "delivery_delayed",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.failed": "failed",
}
#: terminal/negative statuses are never overwritten by a late "delivered"/"sent"
_STICKY = {"bounced", "complained", "failed"}


def init_db(database):
    global db
    db = database


class EmailActionRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=2048)
    comment: Optional[str] = Field(None, max_length=2000)


def _error(exc: ApprovalTokenError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content={"detail": exc.message, "code": exc.code})


@router.get("/approvals/email-action", dependencies=[Depends(_link_rate_limit)])
async def preview_email_action(token: str):
    """Show what the link will do. Never changes state."""
    try:
        claims, _doc, approval = await approval_service.verify_action_token(db, token)
    except ApprovalTokenError as exc:
        return _error(exc)
    return {
        "action": claims.action,
        "approvalId": approval["id"],
        "contractId": approval["contractId"],
        "contractTitle": approval.get("contractTitle"),
        "requesterEmail": approval.get("requesterEmail"),
        "approverEmail": approval.get("approverEmail"),
        "status": approval["status"],
        "expiresAt": claims.expires_at.isoformat(),
    }


@router.post("/approvals/email-action", dependencies=[Depends(_link_rate_limit)])
async def perform_email_action(body: EmailActionRequest, request: Request, background: BackgroundTasks):
    try:
        approval = await approval_service.consume_token_and_decide(
            db, body.token, client_key(request), body.comment
        )
    except ApprovalTokenError as exc:
        return _error(exc)

    # Email the requester too (in-app notification is already created). Runs
    # after the response; failures are logged, never surfaced.
    if approval.get("requesterEmail"):
        subject, html = approval_service.render_decision_email(approval)
        notification = await _new_notification(approval, "approval_decision", approval["requesterEmail"])
        background.add_task(dispatch_email, db, notification["id"], approval["organizationId"],
                            approval["requesterEmail"], subject, html)
    return {"status": approval["status"], "approvalId": approval["id"], "contractId": approval["contractId"],
            "message": f"Contract {approval['status']}."}


async def _new_notification(approval: dict, kind: str, to: str) -> dict:
    import uuid
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    doc = {"id": str(uuid.uuid4()), "organizationId": approval["organizationId"], "approvalId": approval["id"],
           "kind": kind, "to": to, "status": "queued", "resendEmailId": None, "attempts": 0, "events": [],
           "createdAt": now, "updatedAt": now}
    await db.email_notifications.insert_one(dict(doc))
    return doc


@router.post("/webhooks/resend")
async def resend_status_webhook(request: Request):
    """Record Resend delivery events (delivered/bounced/complained/...) on the
    matching ``email_notifications`` record. Svix-signed; fails closed."""
    secret = os.environ.get("RESEND_WEBHOOK_SECRET")
    if not secret:
        logger.error("RESEND_WEBHOOK_SECRET not configured; rejecting Resend webhook")
        raise HTTPException(status_code=503, detail="Webhook not configured")
    body = await request.body()
    try:
        svix.verify(secret, request.headers, body)
    except svix.WebhookVerificationError as exc:
        logger.warning("Resend webhook rejected: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("type", "")
    data = event.get("data") or {}
    email_id = data.get("email_id")
    status = RESEND_EVENT_STATUS.get(event_type)
    if not status or not email_id:
        return {"received": True, "ignored": True}

    notification = await db.email_notifications.find_one({"resendEmailId": email_id}, {"_id": 0})
    if not notification:
        return {"received": True, "matched": False}
    record = {"type": event_type, "at": event.get("created_at"), "svixId": request.headers.get("svix-id")}
    if event_type == "email.bounced":
        record["bounce"] = data.get("bounce")
    update = {"$push": {"events": record}, "$set": {"lastEventAt": event.get("created_at")}}
    if notification.get("status") not in _STICKY:
        update["$set"]["status"] = status
    # scoped by the record's own tenant
    # Scoped by the record's own tenant; the svixId guard makes Svix retries
    # (same svix-id) idempotent.
    await db.email_notifications.update_one(
        {"id": notification["id"], "organizationId": notification["organizationId"],
         "events.svixId": {"$ne": record["svixId"]}},
        update,
    )
    return {"received": True, "matched": True, "status": update["$set"].get("status", notification["status"])}
