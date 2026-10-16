"""Contract-review approval requests with one-click email Approve/Reject links.

Flow
----
1. ``request_approval`` (called when a contract is submitted for review and
   ``APPROVAL_EMAILS_ENABLED`` is on) creates an ``approvals`` record, picks the
   approver, mints one token per action and queues the email.
2. The email links land on the frontend ``/approvals/respond`` page, which calls
   ``GET /api/v1/approvals/email-action`` (preview, no state change -- so link
   scanners cannot approve anything) and then ``POST`` to act.
3. ``consume_token_and_decide`` verifies the token, consumes it atomically and
   applies the decision idempotently (only a ``pending`` approval changes).

Tokens
------
``<payload_b64>.<sig_b64>`` where payload is ``{jti, aid, sub, org, act, exp}``
and sig is HMAC-SHA256 under ``APPROVAL_TOKEN_SECRET`` (falls back to
``JWT_SECRET``). Only ``sha256(token)`` is stored, in ``approval_tokens``,
together with the binding (approval, approver, org, action) and ``usedAt``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ACTIONS = ("approve", "reject")
DECISION_STATUS = {"approve": "approved", "reject": "rejected"}
#: contract status after each decision (mirrors routes.workflow action_map)
CONTRACT_STATUS = {"approve": "approved", "reject": "draft"}
APPROVER_ROLES = ("manager", "admin")


def _ttl_hours() -> int:
    try:
        return max(1, int(os.environ.get("APPROVAL_TOKEN_TTL_HOURS", "72")))
    except ValueError:
        return 72


def _current_key() -> bytes:
    """Signing key. APPROVAL_TOKEN_SECRET if set, else a domain-separated key
    derived from JWT_SECRET (so an approval token can never collide with a JWT)."""
    explicit = os.environ.get("APPROVAL_TOKEN_SECRET")
    if explicit:
        return explicit.encode("utf-8")
    derived = _jwt_derived_key()
    if derived is None:
        raise RuntimeError("APPROVAL_TOKEN_SECRET or JWT_SECRET must be set")
    return derived


def _kid(key: bytes) -> str:
    return hashlib.sha256(b"kid:" + key).hexdigest()[:12]


def _verification_keys() -> Dict[str, bytes]:
    """kid -> key for the current key and APPROVAL_TOKEN_SECRET_PREVIOUS (rotation)."""
    keys = {}
    current = _current_key()
    keys[_kid(current)] = current
    previous = os.environ.get("APPROVAL_TOKEN_SECRET_PREVIOUS")
    if previous:
        keys.setdefault(_kid(previous.encode("utf-8")), previous.encode("utf-8"))
    # Links minted before APPROVAL_TOKEN_SECRET was introduced were signed with
    # the JWT-derived key; keep accepting it so switching doesn't kill them.
    derived = _jwt_derived_key()
    if derived is not None:
        keys.setdefault(_kid(derived), derived)
    return keys


def _jwt_derived_key() -> Optional[bytes]:
    jwt_secret = os.environ.get("JWT_SECRET")
    if not jwt_secret:
        return None
    return hmac.new(jwt_secret.encode("utf-8"), b"approval-token-v1", hashlib.sha256).digest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ApprovalTokenError(Exception):
    """Token is unusable. ``code`` is one of invalid / expired / used /
    mismatch / not_found / decided; ``message`` is safe to show to the user."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass
class TokenClaims:
    jti: str
    approval_id: str
    approver_id: str
    organization_id: str
    action: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# Token create / verify
# ---------------------------------------------------------------------------
def create_action_token(approval_id: str, approver_id: str, organization_id: str, action: str,
                        ttl_hours: Optional[int] = None, now: Optional[datetime] = None,
                        expires_at: Optional[datetime] = None, seed: Optional[str] = None
                        ) -> tuple[str, Dict[str, Any]]:
    """Return ``(raw_token, token_doc)``. Persist ``token_doc``, never the raw token.

    With ``seed`` (the notification id) and a fixed ``expires_at`` the token is
    deterministic, so a retried send re-renders the *same* links instead of
    minting new ones (idempotent retries, no duplicate valid links)."""
    if action not in ACTIONS:
        raise ValueError(f"invalid action {action!r}")
    now = now or _now()
    expires_at = expires_at or (now + timedelta(hours=ttl_hours or _ttl_hours()))
    key = _current_key()
    if seed:
        jti = _b64e(hmac.new(key, f"jti:{seed}:{approval_id}:{action}".encode(), hashlib.sha256).digest()[:16])
    else:
        jti = secrets.token_urlsafe(16)
    payload = {
        "kid": _kid(key),
        "jti": jti,
        "aid": approval_id,
        "sub": approver_id,
        "org": organization_id,
        "act": action,
        "exp": int(expires_at.timestamp()),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64e(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    token = f"{body}.{sig}"
    doc = {
        "tokenHash": hash_token(token),
        "jti": jti,
        "kid": payload["kid"],
        "approvalId": approval_id,
        "approverId": approver_id,
        "organizationId": organization_id,
        "action": action,
        "expiresAt": expires_at.isoformat(),
        "createdAt": now.isoformat(),
        "usedAt": None,
        "revokedAt": None,
    }
    return token, doc


def decode_action_token(token: str, now: Optional[datetime] = None) -> TokenClaims:
    """Check signature, shape and expiry. Does NOT touch the database."""
    try:
        body, sig = token.split(".")
        payload = json.loads(_b64d(body))
        key = _verification_keys().get(str(payload.get("kid")))
        if key is None:
            raise ValueError("unknown kid")
        expected = _b64e(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, sig):
            raise ValueError("bad signature")
        claims = TokenClaims(
            jti=str(payload["jti"]),
            approval_id=str(payload["aid"]),
            approver_id=str(payload["sub"]),
            organization_id=str(payload["org"]),
            action=str(payload["act"]),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc),
        )
    except Exception:
        raise ApprovalTokenError("invalid", "This approval link is invalid or has been tampered with.", 400)
    if claims.action not in ACTIONS:
        raise ApprovalTokenError("invalid", "This approval link is invalid or has been tampered with.", 400)
    if (now or _now()) >= claims.expires_at:
        raise ApprovalTokenError("expired", "This approval link has expired. Please review the contract in LexiSense.", 410)
    return claims


async def verify_action_token(
    db, token: str, now: Optional[datetime] = None, enforce: bool = False
) -> tuple[TokenClaims, Dict[str, Any], Dict[str, Any]]:
    """Full verification (signature, expiry, stored binding, single use,
    tenant, approver eligibility, contract still in review). Returns
    ``(claims, token_doc, approval)``. Read-only unless ``enforce`` is True
    (POST confirm only), in which case an ineligible approver revokes the
    links and a stale contract supersedes the approval. The preview never
    mutates anything."""
    claims = decode_action_token(token, now=now)
    doc = await db.approval_tokens.find_one({"tokenHash": hash_token(token)}, {"_id": 0})
    if not doc:
        raise ApprovalTokenError("not_found", "This approval link is invalid or has been revoked.", 404)
    binding = (doc.get("approvalId"), doc.get("approverId"), doc.get("organizationId"), doc.get("action"))
    if binding != (claims.approval_id, claims.approver_id, claims.organization_id, claims.action):
        raise ApprovalTokenError("mismatch", "This approval link is invalid or has been tampered with.", 400)
    if doc.get("usedAt") or doc.get("revokedAt"):
        raise ApprovalTokenError("used", "This approval link has already been used.", 409)
    approval = await db.approvals.find_one(
        {"id": claims.approval_id, "organizationId": claims.organization_id}, {"_id": 0}
    )
    if not approval or approval.get("approverId") != claims.approver_id:
        raise ApprovalTokenError("mismatch", "This approval link is invalid or has been tampered with.", 400)
    if approval.get("status") != "pending":
        raise ApprovalTokenError("decided", f"This request has already been {approval.get('status')}.", 409)
    if not await _approver_still_eligible(db, approval):
        if enforce:
            await revoke_tokens(db, approval["id"], approval["organizationId"])
        raise ApprovalTokenError("forbidden", "You are no longer allowed to decide this request.", 403)
    contract = await db.contracts.find_one(
        {"id": approval["contractId"], "organizationId": approval["organizationId"]}, {"_id": 0, "status": 1}
    )
    if not contract or contract.get("status") != "review":
        if enforce:
            await supersede(db, approval, "contract_not_in_review")
        raise ApprovalTokenError("stale", "This contract is no longer awaiting review; nothing was changed.", 409)
    return claims, doc, approval


async def _approver_still_eligible(db, approval: dict) -> bool:
    if approval.get("approverId") == approval.get("requesterId"):
        return False
    user = await db.users.find_one(
        {"id": approval["approverId"], "organizationId": approval["organizationId"]},
        {"_id": 0, "role": 1, "isActive": 1},
    )
    return bool(user) and user.get("isActive", True) is not False and user.get("role") in APPROVER_ROLES


async def supersede(db, approval: dict, reason: str) -> None:
    now = _now().isoformat()
    await db.approvals.update_one(
        {"id": approval["id"], "organizationId": approval["organizationId"], "status": "pending"},
        {"$set": {"status": "superseded", "supersededReason": reason, "updatedAt": now},
         "$push": {"auditTrail": {"event": "superseded", "reason": reason, "at": now}}},
    )
    await revoke_tokens(db, approval["id"], approval["organizationId"])


# ---------------------------------------------------------------------------
# Approver selection & request creation
# ---------------------------------------------------------------------------
async def choose_approver(db, organization_id: str, requester_id: str, preferred_id: Optional[str] = None,
                          contract: Optional[dict] = None) -> Optional[dict]:
    """Explicit approver (request param), else the contract's
    ``assignedManagerId``, else the first active manager, else the first admin.
    The requester never approves their own submission."""
    base = {"organizationId": organization_id, "role": {"$in": list(APPROVER_ROLES)},
            "isActive": {"$ne": False}, "id": {"$ne": requester_id}}
    proj = {"_id": 0, "id": 1, "email": 1, "firstName": 1, "lastName": 1, "role": 1}
    for candidate in (preferred_id, (contract or {}).get("assignedManagerId")):
        if candidate and candidate != requester_id:
            # $and keeps the "not the requester" clause; merging {"id": ...}
            # into ``base`` would overwrite it (self-approval bypass).
            user = await db.users.find_one({"$and": [base, {"id": candidate}]}, proj)
            if user:
                return user
    for role in APPROVER_ROLES:
        users = await db.users.find({**base, "role": role}, proj).sort("createdAt", 1).to_list(1)
        if users:
            return users[0]
    return None


async def request_approval(db, contract: dict, requester: dict, approver_id: Optional[str] = None,
                           comment: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Create the approval + notification record. Tokens are minted (and the
    email rendered) at send time by :func:`mint_tokens_for_notification`, so no
    raw token ever passes through the task queue. Returns ``None`` when no
    approver exists. Supersedes any older pending request."""
    org_id = requester["organizationId"]
    approver = await choose_approver(db, org_id, requester["sub"], approver_id, contract)
    if not approver:
        logger.warning("No approver available for contract %s in org %s", contract.get("id"), org_id)
        return None

    now = _now()
    old = await db.approvals.find(
        {"organizationId": org_id, "contractId": contract["id"], "status": "pending"}, {"_id": 0, "id": 1}
    ).to_list(50)
    for prev in old:
        await db.approvals.update_one({"id": prev["id"], "organizationId": org_id, "status": "pending"},
                                      {"$set": {"status": "superseded", "updatedAt": now.isoformat()}})
        await revoke_tokens(db, prev["id"], org_id)

    approval = {
        "id": str(uuid.uuid4()),
        "organizationId": org_id,
        "contractId": contract["id"],
        "contractTitle": contract.get("title"),
        "requesterId": requester["sub"],
        "requesterEmail": requester.get("email"),
        "approverId": approver["id"],
        "approverEmail": approver.get("email"),
        "status": "pending",
        "comment": comment,
        "createdAt": now.isoformat(),
        "updatedAt": now.isoformat(),
        "decidedAt": None,
        "decidedBy": None,
        "auditTrail": [{"event": "requested", "by": requester["sub"], "at": now.isoformat()}],
    }
    await db.approvals.insert_one(dict(approval))

    approval["expiresAt"] = (now + timedelta(hours=_ttl_hours())).isoformat()
    await db.approvals.update_one({"id": approval["id"]}, {"$set": {"expiresAt": approval["expiresAt"]}})

    notification = {
        "id": str(uuid.uuid4()),
        "organizationId": org_id,
        "approvalId": approval["id"],
        "kind": "approval_request",
        "to": approver.get("email"),
        "status": "queued",
        "resendEmailId": None,
        "attempts": 0,
        "events": [],
        "createdAt": now.isoformat(),
        "updatedAt": now.isoformat(),
    }
    await db.email_notifications.insert_one(dict(notification))
    approval.pop("_id", None)
    return {"approval": approval, "notification": notification, "approver": approver}


async def mint_tokens_for_notification(db, approval: dict, notification_id: str) -> Dict[str, str]:
    """Deterministically (re)mint this notification's two tokens and upsert
    their hashes. Calling it again (a retry) yields the identical tokens."""
    expires_at = datetime.fromisoformat(approval["expiresAt"])
    tokens = {}
    for action in ACTIONS:
        raw, doc = create_action_token(approval["id"], approval["approverId"], approval["organizationId"], action,
                                       expires_at=expires_at, seed=notification_id)
        await db.approval_tokens.update_one({"tokenHash": doc["tokenHash"]}, {"$setOnInsert": doc}, upsert=True)
        tokens[action] = raw
    return tokens


async def revoke_tokens(db, approval_id: str, organization_id: str) -> None:
    await db.approval_tokens.update_many(
        {"approvalId": approval_id, "organizationId": organization_id, "usedAt": None, "revokedAt": None},
        {"$set": {"revokedAt": _now().isoformat()}},
    )


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------
async def apply_decision(db, approval: dict, action: str, actor_id: str, actor_email: Optional[str],
                         ip: Optional[str], via: str, comment: Optional[str] = None) -> Optional[dict]:
    """Move a *pending* approval to approved/rejected (conditional update, so
    concurrent or repeated calls are no-ops). For email-link decisions the
    contract must still be in ``review``: if the contract update matches
    nothing, the approval is moved to ``superseded`` and :class:`ApprovalTokenError`
    (``stale``) is raised, so approval and contract can never disagree.
    Returns the updated approval, or ``None`` if it was no longer pending."""
    now = _now().isoformat()
    org_id = approval["organizationId"]
    entry = {"event": DECISION_STATUS[action], "by": actor_id, "email": actor_email, "ip": ip,
             "via": via, "at": now, "comment": comment}
    from pymongo import ReturnDocument

    updated = await db.approvals.find_one_and_update(
        {"id": approval["id"], "organizationId": org_id, "status": "pending"},
        {"$set": {"status": DECISION_STATUS[action], "decidedAt": now, "decidedBy": actor_id,
                  "decidedIp": ip, "decidedVia": via, "updatedAt": now},
         "$push": {"auditTrail": entry}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        return None
    updated.pop("_id", None)

    if via == "email_link":
        # The in-app workflow route updates the contract itself.
        new_status = CONTRACT_STATUS[action]
        contract_update = {
            "$set": {"status": new_status, "updatedAt": now},
            "$push": {"workflowHistory": {"fromStatus": "review", "toStatus": new_status, "action": action,
                                          "userId": actor_id, "userEmail": actor_email, "comment": comment,
                                          "timestamp": now, "via": via, "approvalId": approval["id"]}},
        }
        if action == "approve":
            contract_update["$set"].update({"approvedBy": actor_id, "approvedAt": now})
        try:
            result = await db.contracts.update_one(
                {"id": approval["contractId"], "organizationId": org_id, "status": "review"}, contract_update
            )
            changed = result.modified_count == 1
        except Exception:
            # Ambiguous: the write may or may not have been applied (e.g. a
            # network error after the server committed). Re-read and keep the
            # decision if the contract already reached the target status.
            changed = await _contract_reached(db, approval, org_id, new_status)
            if not changed:
                # Put the approval back so the (un-claimed) link can be retried.
                await db.approvals.update_one(
                    {"id": approval["id"], "organizationId": org_id, "status": DECISION_STATUS[action]},
                    {"$set": {"status": "pending", "decidedAt": None, "decidedBy": None, "decidedIp": None,
                              "decidedVia": None, "updatedAt": now},
                     "$pull": {"auditTrail": {"at": now, "event": DECISION_STATUS[action]}}},
                )
                raise
            logger.warning("Contract update for approval %s errored but was applied; keeping decision",
                           approval["id"])
        if not changed:
            await db.approvals.update_one(
                {"id": approval["id"], "organizationId": org_id},
                {"$set": {"status": "superseded", "supersededReason": "contract_not_in_review", "updatedAt": now},
                 "$push": {"auditTrail": {"event": "superseded", "reason": "contract_not_in_review", "at": now}}},
            )
            await revoke_tokens(db, approval["id"], org_id)
            raise ApprovalTokenError("stale", "This contract is no longer awaiting review; nothing was changed.", 409)
    await _post_commit(db, approval, action, actor_id, actor_email, ip, via, comment)
    return updated


async def _contract_reached(db, approval: dict, org_id: str, new_status: str) -> bool:
    try:
        current = await db.contracts.find_one(
            {"id": approval["contractId"], "organizationId": org_id}, {"_id": 0, "status": 1, "workflowHistory": 1}
        )
    except Exception:
        logger.exception("Could not re-read contract %s after an ambiguous update", approval["contractId"])
        return False
    if not current or current.get("status") != new_status:
        return False
    history = current.get("workflowHistory") or []
    return any(h.get("approvalId") == approval["id"] for h in history)


async def _post_commit(db, approval, action, actor_id, actor_email, ip, via, comment) -> None:
    """Side effects after the decision is committed. They must never raise:
    a failure here can't undo the decision, so it is logged and swallowed."""
    org_id = approval["organizationId"]
    try:
        await revoke_tokens(db, approval["id"], org_id)
    except Exception:
        logger.exception("Failed to revoke tokens for approval %s", approval["id"])

    from services.audit_service import log_action, send_notification

    try:
        await log_action(organization_id=org_id, user_id=actor_id, user_email=actor_email,
                         action=f"contract_{action}", resource_type="contract", resource_id=approval["contractId"],
                         resource_title=approval.get("contractTitle"),
                         details={"approvalId": approval["id"], "via": via, "ip": ip, "comment": comment})
    except Exception:
        logger.exception("Audit log failed for approval %s", approval["id"])
    if approval.get("requesterId") and approval["requesterId"] != actor_id:
        try:
            await send_notification(
                organization_id=org_id, user_id=approval["requesterId"], notification_type=f"workflow_{action}",
                title=f"Contract {DECISION_STATUS[action]}",
                message=f'"{approval.get("contractTitle")}" was {DECISION_STATUS[action]} by {actor_email}',
                resource_type="contract", resource_id=approval["contractId"],
            )
        except Exception:
            logger.exception("Requester notification failed for approval %s", approval["id"])


async def consume_token_and_decide(db, token: str, ip: Optional[str], comment: Optional[str] = None) -> dict:
    """Verify, claim the token (single use), decide. MongoDB transactions need
    a replica set and aren't guaranteed in every deployment, so instead of a
    transaction the claim is released if the decision raises unexpectedly."""
    claims, doc, approval = await verify_action_token(db, token, enforce=True)
    claimed = await db.approval_tokens.update_one(
        {"tokenHash": doc["tokenHash"], "usedAt": None, "revokedAt": None},
        {"$set": {"usedAt": _now().isoformat(), "usedIp": ip}},
    )
    if claimed.modified_count != 1:
        raise ApprovalTokenError("used", "This approval link has already been used.", 409)
    try:
        approver = await db.users.find_one(
            {"id": claims.approver_id, "organizationId": claims.organization_id}, {"_id": 0, "email": 1}
        ) or {}
        updated = await apply_decision(db, approval, claims.action, claims.approver_id,
                                       approver.get("email") or approval.get("approverEmail"), ip,
                                       "email_link", comment)
    except ApprovalTokenError:
        raise  # deliberate outcome (e.g. stale): token stays consumed/revoked
    except Exception:
        # Release the claim only if the approval is (back to) pending, i.e. the
        # decision was not committed or was rolled back. If its state is
        # unknown or decided, the token stays consumed.
        try:
            current = await db.approvals.find_one(
                {"id": approval["id"], "organizationId": approval["organizationId"]}, {"_id": 0, "status": 1}
            )
            if current and current.get("status") == "pending":
                await db.approval_tokens.update_one(
                    {"tokenHash": doc["tokenHash"], "revokedAt": None}, {"$set": {"usedAt": None, "usedIp": None}}
                )
        except Exception:
            logger.exception("Could not release token claim for approval %s", approval["id"])
        raise
    if not updated:
        raise ApprovalTokenError("decided", "This request has already been decided.", 409)
    return updated


async def sync_in_app_decision(db, contract_id: str, organization_id: str, action: str, actor: dict,
                               comment: Optional[str] = None) -> None:
    """Called by the in-app workflow route so a pending email approval is
    closed (and its links revoked) when someone decides inside the app."""
    pending = await db.approvals.find(
        {"organizationId": organization_id, "contractId": contract_id, "status": "pending"}, {"_id": 0}
    ).to_list(10)
    for approval in pending:
        updated = await db.approvals.find_one_and_update(
            {"id": approval["id"], "organizationId": organization_id, "status": "pending"},
            {"$set": {"status": DECISION_STATUS[action], "decidedAt": _now().isoformat(),
                      "decidedBy": actor["sub"], "decidedVia": "in_app", "updatedAt": _now().isoformat()},
             "$push": {"auditTrail": {"event": DECISION_STATUS[action], "by": actor["sub"],
                                      "email": actor.get("email"), "via": "in_app",
                                      "at": _now().isoformat(), "comment": comment}}},
        )
        if updated:
            await revoke_tokens(db, approval["id"], organization_id)


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------
def _app_url() -> str:
    return os.environ.get("APP_URL", "").rstrip("/")


def action_link(token: str) -> str:
    # Fragment, not query: never sent to any server, so it can't reach access
    # logs, proxies or Referer headers. The SPA strips it immediately.
    return f"{_app_url()}/approvals/respond#token={token}"


def render_request_email(approval: dict, approver: Optional[dict], tokens: Dict[str, str]) -> tuple[str, str]:
    title = escape(approval.get("contractTitle") or "Untitled contract")
    requester = escape(approval.get("requesterEmail") or "A teammate")
    comment = escape(approval.get("comment") or "")
    view = f"{_app_url()}/contracts/{approval['contractId']}"
    btn = ("display:inline-block;padding:12px 28px;margin:0 6px;color:#fff;text-decoration:none;"
           "font-weight:600;border-radius:8px;background-color:{}")
    subject = f"Approval requested: {approval.get('contractTitle') or 'Contract'}"
    html = f"""<!DOCTYPE html><html><body style="font-family:Segoe UI,Arial,sans-serif;background:#0f1318;padding:32px">
<table width="600" align="center" cellpadding="0" cellspacing="0" style="background:#1a1f26;border-radius:12px;color:#e5e7eb">
<tr><td style="padding:32px"><h1 style="color:#2563eb;margin:0 0 16px">LexiSense</h1>
<p style="font-size:16px"><strong>{requester}</strong> has requested your approval for
<strong>{title}</strong>.</p>
{f'<p style="color:#9ca3af">Note: {comment}</p>' if comment else ''}
<p style="text-align:center;margin:28px 0">
<a href="{escape(action_link(tokens['approve']), quote=True)}" style="{btn.format('#16a34a')}">Approve</a>
<a href="{escape(action_link(tokens['reject']), quote=True)}" style="{btn.format('#dc2626')}">Reject</a></p>
<p style="text-align:center"><a href="{escape(view, quote=True)}" style="color:#60a5fa">View the contract in LexiSense</a></p>
<p style="color:#6b7280;font-size:12px;text-align:center">These links can be used once and expire in
{_ttl_hours()} hours. You will be asked to confirm before anything changes.</p>
</td></tr></table></body></html>"""
    return subject, html


def render_decision_email(approval: dict) -> tuple[str, str]:
    title = escape(approval.get("contractTitle") or "Contract")
    status = approval.get("status")
    view = f"{_app_url()}/contracts/{approval['contractId']}"
    subject = f"Contract {status}: {approval.get('contractTitle') or 'Contract'}"
    html = (f"<p><strong>{title}</strong> was {escape(str(status))} by "
            f"{escape(approval.get('approverEmail') or 'your approver')}.</p>"
            f'<p><a href="{escape(view, quote=True)}">View the contract in LexiSense</a></p>')
    return subject, html
