"""Approval email links (APPROVAL_EMAILS_ENABLED): token lifecycle, the public
email-action endpoints, the workflow hook, the Resend (Svix) webhook, and
email failures never breaking the request. Resend is always mocked."""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from routes import approvals as approvals_route
from routes import workflow as workflow_route
from services import approval_service, approval_tasks, audit_service, email_service
from services.approval_service import ApprovalTokenError
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract, make_user
from utils import svix

WEBHOOK_SECRET = "whsec_" + __import__("base64").b64encode(b"super-secret-key-for-tests").decode()


@pytest.fixture
def sent(monkeypatch):
    """Capture outbound email instead of calling Resend."""
    calls = []

    async def fake_send(to, subject, html, tags=None):
        calls.append({"to": to, "subject": subject, "html": html})
        return {"status": "sent", "email_id": f"re_{len(calls)}"}

    monkeypatch.setattr(email_service, "send_raw_email", fake_send)
    monkeypatch.setattr("services.celery_app.CELERY_BROKER_URL", None, raising=False)
    return calls


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("APPROVAL_EMAILS_ENABLED", "true")
    monkeypatch.setenv("APP_URL", "https://app.example.com")
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", WEBHOOK_SECRET)


@pytest.fixture
async def http(db, flag_on, sent):
    workflow_route.init_db(db)
    approvals_route.init_db(db)
    audit_service.init_db(db)
    approvals_route.APPROVAL_LINK_LIMITER.reset()
    app = FastAPI()
    router = APIRouter(prefix=API_PREFIX)
    router.include_router(workflow_route.router)
    router.include_router(approvals_route.router)
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def seeded(db, org_id, other_org_id):
    requester = make_user(org_id, role="user", user_id="u-req", email="req@example.com")
    manager = make_user(org_id, role="manager", user_id="u-mgr", email="mgr@example.com")
    outsider = make_user(other_org_id, role="manager", user_id="u-out", email="out@example.com")
    for u in (requester, manager, outsider):
        await db.users.insert_one(dict(u))
    contract = make_contract(org_id, requester["id"], contract_id="c-1", status="draft")
    await db.contracts.insert_one(dict(contract))
    return {"requester": requester, "manager": manager, "outsider": outsider, "contract": contract}


def _token_from(html, action_index):
    import re
    links = re.findall(r"/approvals/respond\?token=([A-Za-z0-9_\-\.]+)", html)
    return links[action_index]


async def _submit(http, seeded):
    r = await http.post(f"{API_PREFIX}/contracts/c-1/workflow/submit_for_review",
                        headers=auth_headers(seeded["requester"]))
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- tokens
def test_token_roundtrip_and_binding():
    token, doc = approval_service.create_action_token("a1", "u1", "org1", "approve")
    claims = approval_service.decode_action_token(token)
    assert (claims.approval_id, claims.approver_id, claims.organization_id, claims.action) == \
        ("a1", "u1", "org1", "approve")
    assert doc["tokenHash"] == approval_service.hash_token(token)
    assert token not in json.dumps(doc)  # raw token never stored


def test_token_expired():
    past = datetime.now(timezone.utc) - timedelta(hours=100)
    token, _ = approval_service.create_action_token("a1", "u1", "org1", "approve", ttl_hours=72, now=past)
    with pytest.raises(ApprovalTokenError) as e:
        approval_service.decode_action_token(token)
    assert e.value.code == "expired"


def test_token_ttl_configurable(monkeypatch):
    monkeypatch.setenv("APPROVAL_TOKEN_TTL_HOURS", "1")
    token, _ = approval_service.create_action_token("a1", "u1", "org1", "approve")
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    with pytest.raises(ApprovalTokenError):
        approval_service.decode_action_token(token, now=later)


@pytest.mark.parametrize("mutate", [
    lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),
    lambda t: "x" + t,
    lambda t: t.split(".")[0],
    lambda t: "garbage",
])
def test_token_tampered(mutate):
    token, _ = approval_service.create_action_token("a1", "u1", "org1", "approve")
    with pytest.raises(ApprovalTokenError) as e:
        approval_service.decode_action_token(mutate(token))
    assert e.value.code == "invalid"


def test_token_payload_swap_rejected():
    """Changing the action in the payload without re-signing fails."""
    import base64
    token, _ = approval_service.create_action_token("a1", "u1", "org1", "reject")
    body, sig = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=="))
    payload["act"] = "approve"
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode() + "." + sig
    with pytest.raises(ApprovalTokenError):
        approval_service.decode_action_token(forged)


async def test_verify_tenant_mismatch(db):
    token, doc = approval_service.create_action_token("a1", "u1", "org1", "approve")
    doc["organizationId"] = "org2"  # stored binding differs from signed claims
    await db.approval_tokens.insert_one(doc)
    with pytest.raises(ApprovalTokenError) as e:
        await approval_service.verify_action_token(db, token)
    assert e.value.code == "mismatch"


async def test_verify_approval_in_other_tenant_not_found(db):
    token, doc = approval_service.create_action_token("a1", "u1", "org1", "approve")
    await db.approval_tokens.insert_one(doc)
    await db.approvals.insert_one({"id": "a1", "organizationId": "org2", "approverId": "u1", "status": "pending"})
    with pytest.raises(ApprovalTokenError) as e:
        await approval_service.verify_action_token(db, token)
    assert e.value.code == "mismatch"


async def test_unknown_token_hash(db):
    token, _ = approval_service.create_action_token("a1", "u1", "org1", "approve")
    with pytest.raises(ApprovalTokenError) as e:
        await approval_service.verify_action_token(db, token)
    assert e.value.code == "not_found"


# --------------------------------------------------------------------------- endpoints
async def test_submit_sends_email_with_links(http, seeded, sent, db):
    body = await _submit(http, seeded)
    assert body["approval"]["approverId"] == "u-mgr"
    assert len(sent) == 1 and sent[0]["to"] == "mgr@example.com"
    html = sent[0]["html"]
    assert "https://app.example.com/contracts/c-1" in html
    assert html.count("/approvals/respond?token=") == 2
    notif = await db.email_notifications.find_one({"approvalId": body["approval"]["id"]})
    assert notif["status"] == "sent" and notif["resendEmailId"] == "re_1"
    assert await db.approval_tokens.count_documents({}) == 2


async def test_preview_does_not_change_state(http, seeded, sent, db):
    await _submit(http, seeded)
    token = _token_from(sent[0]["html"], 0)
    for _ in range(3):
        r = await http.get(f"{API_PREFIX}/approvals/email-action", params={"token": token})
        assert r.status_code == 200 and r.json()["action"] == "approve"
    assert (await db.approvals.find_one({}))["status"] == "pending"
    assert (await db.contracts.find_one({"id": "c-1"}))["status"] == "review"


async def test_approve_via_link(http, seeded, sent, db):
    await _submit(http, seeded)
    token = _token_from(sent[0]["html"], 0)
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": token},
                        headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    approval = await db.approvals.find_one({})
    assert approval["decidedBy"] == "u-mgr" and approval["decidedIp"] == "203.0.113.9"
    assert approval["auditTrail"][-1]["event"] == "approved"
    contract = await db.contracts.find_one({"id": "c-1"})
    assert contract["status"] == "approved" and contract["approvedBy"] == "u-mgr"
    assert await db.audit_logs.count_documents({"action": "contract_approve"}) == 1
    assert await db.notifications.count_documents({"userId": "u-req", "type": "workflow_approve"}) == 1
    assert sent[-1]["to"] == "req@example.com"  # requester emailed

    # single use: same token again -> 409, no change
    r2 = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": token})
    assert r2.status_code == 409 and r2.json()["code"] == "used"
    # sibling reject link revoked
    reject = _token_from(sent[0]["html"], 1)
    r3 = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": reject})
    assert r3.status_code == 409
    assert (await db.approvals.find_one({}))["status"] == "approved"


async def test_reject_via_link(http, seeded, sent, db):
    await _submit(http, seeded)
    r = await http.post(f"{API_PREFIX}/approvals/email-action",
                        json={"token": _token_from(sent[0]["html"], 1), "comment": "fix clause 4"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert (await db.contracts.find_one({"id": "c-1"}))["status"] == "draft"


async def test_tampered_and_expired_link_no_state_change(http, seeded, sent, db, monkeypatch):
    await _submit(http, seeded)
    token = _token_from(sent[0]["html"], 0)
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": token[:-3] + "abc"})
    assert r.status_code == 400 and r.json()["code"] == "invalid"
    future = datetime.now(timezone.utc) + timedelta(hours=73)
    monkeypatch.setattr(approval_service, "_now", lambda: future)
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": token})
    assert r.status_code == 410 and r.json()["code"] == "expired"
    assert (await db.approvals.find_one({}))["status"] == "pending"
    assert (await db.contracts.find_one({"id": "c-1"}))["status"] == "review"


async def test_in_app_decision_revokes_links(http, seeded, sent, db):
    await _submit(http, seeded)
    r = await http.post(f"{API_PREFIX}/contracts/c-1/workflow/approve", headers=auth_headers(seeded["manager"]))
    assert r.status_code == 200
    assert (await db.approvals.find_one({}))["status"] == "approved"
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": _token_from(sent[0]["html"], 1)})
    assert r.status_code == 409


async def test_explicit_approver_must_be_same_tenant(http, seeded, sent):
    r = await http.post(f"{API_PREFIX}/contracts/c-1/workflow/submit_for_review",
                        params={"approver_id": "u-out"}, headers=auth_headers(seeded["requester"]))
    assert r.status_code == 200
    assert r.json()["approval"]["approverId"] == "u-mgr"  # outsider ignored
    assert sent[0]["to"] == "mgr@example.com"


async def test_resubmission_supersedes_old_links(http, seeded, sent, db):
    await _submit(http, seeded)
    await http.post(f"{API_PREFIX}/contracts/c-1/workflow/reject", headers=auth_headers(seeded["manager"]))
    await _submit(http, seeded)
    old = _token_from(sent[0]["html"], 0)
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": old})
    assert r.status_code == 409


async def test_email_failure_does_not_break_request(http, seeded, db, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("resend down")
    monkeypatch.setattr(email_service, "send_raw_email", boom)
    body = await _submit(http, seeded)
    assert body["status"] == "review"
    notif = await db.email_notifications.find_one({})
    assert notif["status"] == "failed" and "resend down" in notif["lastError"]


async def test_approval_service_crash_does_not_break_request(http, seeded, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(approval_service, "request_approval", boom)
    body = await _submit(http, seeded)
    assert body["status"] == "review" and "approval" not in body


async def test_flag_off_no_email(http, seeded, sent, monkeypatch, db):
    monkeypatch.setenv("APPROVAL_EMAILS_ENABLED", "false")
    body = await _submit(http, seeded)
    assert "approval" not in body and sent == []
    assert await db.approvals.count_documents({}) == 0


async def test_dispatch_uses_celery_when_configured(db, monkeypatch, sent):
    queued = []
    monkeypatch.setattr("services.celery_app.CELERY_BROKER_URL", "redis://x", raising=False)
    monkeypatch.setattr(approval_tasks.send_approval_email_task, "apply_async",
                        lambda *a, **k: queued.append(k), raising=False)
    status = await approval_tasks.dispatch_email(db, "n1", "org1", "a@b.c", "s", "h")
    assert status == "queued" and queued and sent == []


async def test_dispatch_falls_back_inline_when_enqueue_fails(db, monkeypatch, sent):
    def fail(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr("services.celery_app.CELERY_BROKER_URL", "redis://x", raising=False)
    monkeypatch.setattr(approval_tasks.send_approval_email_task, "apply_async", fail, raising=False)
    await db.email_notifications.insert_one({"id": "n1", "organizationId": "org1", "status": "queued"})
    assert await approval_tasks.dispatch_email(db, "n1", "org1", "a@b.c", "s", "h") == "sent"
    assert len(sent) == 1


# --------------------------------------------------------------------------- webhook
def _signed(body: bytes, secret=WEBHOOK_SECRET, ts=None, msg_id="msg_1"):
    ts = str(int(ts or time.time()))
    return {"svix-id": msg_id, "svix-timestamp": ts, "svix-signature": svix.sign(secret, msg_id, ts, body),
            "content-type": "application/json"}


async def test_webhook_records_delivery_status(http, db):
    await db.email_notifications.insert_one(
        {"id": "n1", "organizationId": "org-alpha", "resendEmailId": "re_9", "status": "sent", "events": []})
    for i, etype in enumerate(["email.delivered", "email.bounced", "email.delivered"]):
        body = json.dumps({"type": etype, "created_at": "2026-10-15T00:00:00Z", "data": {"email_id": "re_9"}}).encode()
        r = await http.post(f"{API_PREFIX}/webhooks/resend", content=body, headers=_signed(body, msg_id=f"m{i}"))
        assert r.status_code == 200, r.text
    notif = await db.email_notifications.find_one({"id": "n1"})
    assert notif["status"] == "bounced"  # sticky: late "delivered" does not overwrite
    assert len(notif["events"]) == 3


async def test_webhook_replay_is_idempotent(http, db):
    await db.email_notifications.insert_one(
        {"id": "n1", "organizationId": "org-alpha", "resendEmailId": "re_9", "status": "sent", "events": []})
    body = json.dumps({"type": "email.complained", "data": {"email_id": "re_9"}}).encode()
    headers = _signed(body)
    for _ in range(2):
        assert (await http.post(f"{API_PREFIX}/webhooks/resend", content=body, headers=headers)).status_code == 200
    notif = await db.email_notifications.find_one({"id": "n1"})
    assert notif["status"] == "complained" and len(notif["events"]) == 1


@pytest.mark.parametrize("headers_fn", [
    lambda b: {},
    lambda b: _signed(b, secret="whsec_" + __import__("base64").b64encode(b"wrong").decode()),
    lambda b: _signed(b, ts=time.time() - 3600),
    lambda b: _signed(b + b" "),
])
async def test_webhook_rejects_bad_signature(http, db, headers_fn):
    body = json.dumps({"type": "email.delivered", "data": {"email_id": "re_9"}}).encode()
    r = await http.post(f"{API_PREFIX}/webhooks/resend", content=body, headers=headers_fn(body))
    assert r.status_code == 401


async def test_webhook_fails_closed_without_secret(http, monkeypatch):
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET")
    r = await http.post(f"{API_PREFIX}/webhooks/resend", content=b"{}")
    assert r.status_code == 503


def test_flag_default_off(monkeypatch):
    from utils.feature_flags import approval_emails_enabled
    monkeypatch.delenv("APPROVAL_EMAILS_ENABLED", raising=False)
    assert approval_emails_enabled() is False
