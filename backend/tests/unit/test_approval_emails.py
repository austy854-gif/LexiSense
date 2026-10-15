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

    async def fake_send(to, subject, html, tags=None, idempotency_key=None):
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
    links = re.findall(r"/approvals/respond#token=([A-Za-z0-9_\-\.]+)", html)
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
    assert html.count("/approvals/respond#token=") == 2 and "?token=" not in html
    notif = await db.email_notifications.find_one({"approvalId": body["approval"]["id"]})
    assert notif["status"] == "sent" and notif["resendEmailId"] == "re_1"
    assert await db.approval_tokens.count_documents({}) == 2


async def test_preview_does_not_change_state(http, seeded, sent, db):
    await _submit(http, seeded)
    token = _token_from(sent[0]["html"], 0)
    for _ in range(3):
        r = await http.post(f"{API_PREFIX}/approvals/email-action/preview", json={"token": token})
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


async def test_dispatch_uses_celery_with_ids_only(db, monkeypatch, sent):
    queued = []
    monkeypatch.setattr("services.celery_app.CELERY_BROKER_URL", "redis://x", raising=False)
    monkeypatch.setattr(approval_tasks.send_approval_email_task, "apply_async",
                        lambda *a, **k: queued.append(k), raising=False)
    status = await approval_tasks.dispatch_notification(db, "n1", "org1")
    assert status == "queued" and sent == []
    assert queued[0]["args"] == ("n1", "org1")  # no token / html in the broker


async def test_dispatch_falls_back_inline_when_enqueue_fails(http, seeded, db, monkeypatch, sent):
    def fail(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr("services.celery_app.CELERY_BROKER_URL", "redis://x", raising=False)
    monkeypatch.setattr(approval_tasks.send_approval_email_task, "apply_async", fail, raising=False)
    await _submit(http, seeded)
    assert len(sent) == 1 and (await db.email_notifications.find_one({}))["status"] == "sent"


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


# =========================================================================== review fixes (PR #42)
# P0-1 self-approval bypass
async def test_self_approval_via_approver_id_blocked(http, db, org_id, sent):
    me = make_user(org_id, role="manager", user_id="u-me", email="me@example.com")
    other = make_user(org_id, role="manager", user_id="u-other", email="other@example.com")
    for u in (me, other):
        await db.users.insert_one(dict(u))
    await db.contracts.insert_one(make_contract(org_id, "u-me", contract_id="c-1", status="draft"))
    r = await http.post(f"{API_PREFIX}/contracts/c-1/workflow/submit_for_review",
                        params={"approver_id": "u-me"}, headers=auth_headers(me))
    assert r.json()["approval"]["approverId"] == "u-other"
    assert sent[0]["to"] == "other@example.com"


async def test_self_approval_via_assigned_manager_blocked(db, org_id):
    me = make_user(org_id, role="manager", user_id="u-me")
    await db.users.insert_one(dict(me))
    picked = await approval_service.choose_approver(db, org_id, "u-me", None, {"assignedManagerId": "u-me"})
    assert picked is None  # the only manager is the requester
    other = make_user(org_id, role="admin", user_id="u-adm")
    await db.users.insert_one(dict(other))
    picked = await approval_service.choose_approver(db, org_id, "u-me", "u-me", {"assignedManagerId": "u-me"})
    assert picked["id"] == "u-adm"


async def test_assigned_manager_used_when_eligible(db, org_id):
    for uid in ("u-m1", "u-m2"):
        await db.users.insert_one(make_user(org_id, role="manager", user_id=uid))
    picked = await approval_service.choose_approver(db, org_id, "u-req", None, {"assignedManagerId": "u-m2"})
    assert picked["id"] == "u-m2"


# P1-2 approver re-checked at POST time
@pytest.mark.parametrize("change", [
    {"isActive": False}, {"role": "user"}, {"organizationId": "org-beta"},
])
async def test_ineligible_approver_gets_403_and_token_revoked(http, seeded, sent, db, change):
    await _submit(http, seeded)
    await db.users.update_one({"id": "u-mgr"}, {"$set": change})
    token = _token_from(sent[0]["html"], 0)
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": token})
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert await db.approval_tokens.count_documents({"revokedAt": None}) == 0
    assert (await db.contracts.find_one({"id": "c-1"}))["status"] == "review"


async def test_deleted_approver_gets_403(http, seeded, sent, db):
    await _submit(http, seeded)
    await db.users.delete_one({"id": "u-mgr"})
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": _token_from(sent[0]["html"], 0)})
    assert r.status_code == 403


# P1-3 token never in logs / referrer
async def test_responses_carry_no_referrer(http, seeded, sent):
    await _submit(http, seeded)
    token = _token_from(sent[0]["html"], 0)
    ok = await http.post(f"{API_PREFIX}/approvals/email-action/preview", json={"token": token})
    bad = await http.post(f"{API_PREFIX}/approvals/email-action/preview", json={"token": "x" * 20})
    for r in (ok, bad):
        assert r.headers["referrer-policy"] == "no-referrer"


async def test_get_with_token_query_not_supported(http):
    r = await http.get(f"{API_PREFIX}/approvals/email-action", params={"token": "abc.def"})
    assert r.status_code == 405


def test_request_log_query_redacted():
    from utils.logging_config import redact_query
    out = redact_query("token=secret.value&page=2")
    assert "secret" not in out and "page=2" in out and "token=" in out


def test_uvicorn_access_log_redacted():
    import logging
    from utils.logging_config import AccessLogRedactionFilter
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4", "GET", "/x?token=abc.def&a=1", "1.1", 200), None)
    AccessLogRedactionFilter().filter(rec)
    assert "abc.def" not in rec.getMessage() and "a=1" in rec.getMessage()


async def test_request_logging_middleware_redacts(caplog):
    from utils.logging_config import RequestLoggingMiddleware

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request"}

    async def send(_):
        pass

    mw = RequestLoggingMiddleware(app)
    with caplog.at_level("INFO", logger="lexisense.request"):
        await mw({"type": "http", "method": "GET", "path": "/p", "query_string": b"token=topsecret",
                  "client": ("1.1.1.1", 1), "headers": []}, receive, send)
    assert all("topsecret" not in str(getattr(r, "query", "")) for r in caplog.records)
    assert any(getattr(r, "query", None) for r in caplog.records)


# P1-4 approval/contract consistency
async def test_contract_moved_out_of_review_supersedes(http, seeded, sent, db):
    await _submit(http, seeded)
    await db.contracts.update_one({"id": "c-1"}, {"$set": {"status": "draft"}})
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": _token_from(sent[0]["html"], 0)})
    assert r.status_code == 409 and r.json()["code"] == "stale"
    assert (await db.approvals.find_one({}))["status"] == "superseded"
    assert (await db.contracts.find_one({"id": "c-1"}))["status"] == "draft"


async def test_contract_changes_between_verify_and_update(http, seeded, sent, db, monkeypatch):
    """Race: contract leaves review after verification -> modified_count 0."""
    await _submit(http, seeded)
    real_verify = approval_service.verify_action_token

    async def verify_then_race(db_, token, now=None, enforce=False):
        out = await real_verify(db_, token, now, enforce)
        await db.contracts.update_one({"id": "c-1"}, {"$set": {"status": "draft"}})
        return out
    monkeypatch.setattr(approval_service, "verify_action_token", verify_then_race)
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": _token_from(sent[0]["html"], 0)})
    assert r.status_code == 409 and r.json()["code"] == "stale"
    assert (await db.approvals.find_one({}))["status"] == "superseded"
    assert (await db.contracts.find_one({"id": "c-1"}))["status"] == "draft"


# P2-5 token un-claimed when the decision fails
async def test_token_unclaimed_on_unexpected_error(http, seeded, sent, db, monkeypatch):
    await _submit(http, seeded)
    token = _token_from(sent[0]["html"], 0)
    coll_cls = type(db.contracts)
    real = coll_cls.update_one
    calls = {"n": 0}

    async def flaky(self, *a, **k):
        if self.name == "contracts":
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("mongo blip")
        return await real(self, *a, **k)
    monkeypatch.setattr(coll_cls, "update_one", flaky)
    with pytest.raises(RuntimeError):
        await approval_service.consume_token_and_decide(db, token, "1.1.1.1")
    doc = await db.approval_tokens.find_one({"tokenHash": approval_service.hash_token(token)})
    assert doc["usedAt"] is None and (await db.approvals.find_one({}))["status"] == "pending"
    # retry now succeeds
    out = await approval_service.consume_token_and_decide(db, token, "1.1.1.1")
    assert out["status"] == "approved"


# P2-6 tokens minted at send time, retries idempotent
async def test_tokens_minted_at_send_time(http, seeded, db, monkeypatch):
    monkeypatch.setattr("services.celery_app.CELERY_BROKER_URL", "redis://x", raising=False)
    monkeypatch.setattr(approval_tasks.send_approval_email_task, "apply_async", lambda *a, **k: None, raising=False)
    await _submit(http, seeded)
    assert await db.approval_tokens.count_documents({}) == 0  # nothing minted at request time


async def test_retry_reuses_same_tokens_and_skips_after_success(http, seeded, db, monkeypatch):
    attempts = []

    async def flaky_send(to, subject, html, tags=None, idempotency_key=None):
        attempts.append((html, idempotency_key))
        if len(attempts) == 1:
            raise RuntimeError("timeout")
        return {"status": "sent", "email_id": "re_x"}
    monkeypatch.setattr(email_service, "send_raw_email", flaky_send)
    await _submit(http, seeded)  # inline attempt 1 fails
    notif = await db.email_notifications.find_one({})
    await approval_tasks.deliver_notification(db, notif["id"], notif["organizationId"])  # retry
    assert attempts[0][0] == attempts[1][0]  # identical links
    assert attempts[0][1] == attempts[1][1] == f"lexisense-notification-{notif['id']}"
    assert await db.approval_tokens.count_documents({}) == 2
    again = await approval_tasks.deliver_notification(db, notif["id"], notif["organizationId"])
    assert again["status"] == "duplicate_skipped" and len(attempts) == 2


# P2-7 key derivation, kid, rotation
def test_default_key_is_domain_separated(monkeypatch):
    import hashlib
    import hmac as _hmac
    monkeypatch.delenv("APPROVAL_TOKEN_SECRET", raising=False)
    key = approval_service._current_key()
    assert key != __import__("os").environ["JWT_SECRET"].encode()
    assert key == _hmac.new(__import__("os").environ["JWT_SECRET"].encode(), b"approval-token-v1",
                            hashlib.sha256).digest()


def test_token_carries_kid_and_rotation_works(monkeypatch):
    monkeypatch.setenv("APPROVAL_TOKEN_SECRET", "old-secret")
    token, doc = approval_service.create_action_token("a1", "u1", "org1", "approve")
    assert doc["kid"]
    monkeypatch.setenv("APPROVAL_TOKEN_SECRET", "new-secret")
    with pytest.raises(ApprovalTokenError):
        approval_service.decode_action_token(token)
    monkeypatch.setenv("APPROVAL_TOKEN_SECRET_PREVIOUS", "old-secret")
    assert approval_service.decode_action_token(token).approval_id == "a1"


# P2-8 forwarded-for handling + webhook rate limit
@pytest.mark.parametrize("hops,xff,expected", [
    ("1", "6.6.6.6, 203.0.113.9", "203.0.113.9"),     # spoofed left entry ignored
    ("2", "6.6.6.6, 203.0.113.9, 10.0.0.2", "203.0.113.9"),
    ("0", "6.6.6.6", "peer"),
    ("3", "203.0.113.9", "peer"),  # fewer entries than hops -> socket peer
])
def test_client_key_trusted_proxies(monkeypatch, hops, xff, expected):
    from types import SimpleNamespace
    from utils.rbac import client_key
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", hops)
    req = SimpleNamespace(headers={"x-forwarded-for": xff}, client=SimpleNamespace(host="peer"))
    assert client_key(req) == expected


async def test_webhook_rate_limited(http, monkeypatch):
    from utils.rbac import SlidingWindowRateLimiter
    monkeypatch.setattr(approvals_route.RESEND_WEBHOOK_LIMITER, "max_requests", 2, raising=False)
    approvals_route.RESEND_WEBHOOK_LIMITER.reset()
    codes = [(await http.post(f"{API_PREFIX}/webhooks/resend", content=b"{}")).status_code for _ in range(3)]
    approvals_route.RESEND_WEBHOOK_LIMITER.reset()
    assert codes[-1] == 429 and SlidingWindowRateLimiter


# P2-9 status order + strict base64
async def test_late_sent_does_not_overwrite_delivered(http, db):
    approvals_route.RESEND_WEBHOOK_LIMITER.reset()
    await db.email_notifications.insert_one(
        {"id": "n1", "organizationId": "org-alpha", "resendEmailId": "re_9", "status": "sent", "events": []})
    for i, etype in enumerate(["email.delivered", "email.sent"]):
        body = json.dumps({"type": etype, "data": {"email_id": "re_9"}}).encode()
        assert (await http.post(f"{API_PREFIX}/webhooks/resend", content=body,
                                headers=_signed(body, msg_id=f"s{i}"))).status_code == 200
    assert (await db.email_notifications.find_one({"id": "n1"}))["status"] == "delivered"


def test_svix_secret_strict_base64():
    with pytest.raises(svix.WebhookVerificationError):
        svix.sign("whsec_not*valid*base64!!", "m", "1", b"x")


# =========================================================================== re-review nits (29fd082)
# 1: post-commit side effects never raise; un-claim only when rolled back
async def test_side_effect_failures_do_not_fail_decision(http, seeded, sent, db, monkeypatch):
    await _submit(http, seeded)

    async def boom(*a, **k):
        raise RuntimeError("audit down")
    monkeypatch.setattr(audit_service, "log_action", boom)
    monkeypatch.setattr(audit_service, "send_notification", boom)
    token = _token_from(sent[0]["html"], 0)
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": token})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert (await db.contracts.find_one({"id": "c-1"}))["status"] == "approved"
    doc = await db.approval_tokens.find_one({"tokenHash": approval_service.hash_token(token)})
    assert doc["usedAt"] is not None  # stays consumed


async def test_token_stays_consumed_when_decision_committed_then_error(http, seeded, sent, db, monkeypatch):
    """An error after the approval committed (and wasn't rolled back) must not
    release the token."""
    await _submit(http, seeded)
    token = _token_from(sent[0]["html"], 0)

    async def explode(*a, **k):
        raise RuntimeError("late failure")
    monkeypatch.setattr(approval_service, "_post_commit", explode)
    with pytest.raises(RuntimeError):
        await approval_service.consume_token_and_decide(db, token, "1.1.1.1")
    assert (await db.approvals.find_one({}))["status"] == "approved"
    doc = await db.approval_tokens.find_one({"tokenHash": approval_service.hash_token(token)})
    assert doc["usedAt"] is not None


# 2: ambiguous contract write that actually applied keeps the decision
async def test_ambiguous_contract_error_keeps_decision_if_applied(http, seeded, sent, db, monkeypatch):
    await _submit(http, seeded)
    token = _token_from(sent[0]["html"], 0)
    coll_cls = type(db.contracts)
    real = coll_cls.update_one

    async def applied_then_error(self, *a, **k):
        res = await real(self, *a, **k)
        if self.name == "contracts":
            raise TimeoutError("network error after commit")
        return res
    monkeypatch.setattr(coll_cls, "update_one", applied_then_error)
    out = await approval_service.consume_token_and_decide(db, token, "1.1.1.1")
    monkeypatch.setattr(coll_cls, "update_one", real)
    assert out["status"] == "approved"
    assert (await db.approvals.find_one({}))["status"] == "approved"
    assert (await db.contracts.find_one({"id": "c-1"}))["status"] == "approved"
    doc = await db.approval_tokens.find_one({"tokenHash": approval_service.hash_token(token)})
    assert doc["usedAt"] is not None


# 3: XFF hardening
def test_client_key_repeated_xff_headers(monkeypatch):
    from starlette.datastructures import Headers
    from types import SimpleNamespace
    from utils.rbac import client_key
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "2")
    headers = Headers(raw=[(b"x-forwarded-for", b"6.6.6.6"), (b"x-forwarded-for", b"203.0.113.9, 10.0.0.2")])
    req = SimpleNamespace(headers=headers, client=SimpleNamespace(host="peer"))
    assert client_key(req) == "203.0.113.9"


def test_client_key_no_xff_uses_peer(monkeypatch):
    from starlette.datastructures import Headers
    from types import SimpleNamespace
    from utils.rbac import client_key
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    assert client_key(SimpleNamespace(headers=Headers(raw=[]), client=SimpleNamespace(host="peer"))) == "peer"


def test_dockerfile_does_not_trust_all_proxies():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[3] / "Dockerfile").read_text()
    assert 'FORWARDED_ALLOW_IPS="*"' not in text and 'FORWARDED_ALLOW_IPS="127.0.0.1"' in text


# 4: preview is read-only
async def test_preview_ineligible_approver_has_no_side_effects(http, seeded, sent, db):
    await _submit(http, seeded)
    await db.users.update_one({"id": "u-mgr"}, {"$set": {"isActive": False}})
    token = _token_from(sent[0]["html"], 0)
    r = await http.post(f"{API_PREFIX}/approvals/email-action/preview", json={"token": token})
    assert r.status_code == 403
    assert await db.approval_tokens.count_documents({"revokedAt": None}) == 2
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": token})
    assert r.status_code == 403
    assert await db.approval_tokens.count_documents({"revokedAt": None}) == 0


async def test_preview_stale_contract_has_no_side_effects(http, seeded, sent, db):
    await _submit(http, seeded)
    await db.contracts.update_one({"id": "c-1"}, {"$set": {"status": "draft"}})
    token = _token_from(sent[0]["html"], 0)
    r = await http.post(f"{API_PREFIX}/approvals/email-action/preview", json={"token": token})
    assert r.status_code == 409 and r.json()["code"] == "stale"
    assert (await db.approvals.find_one({}))["status"] == "pending"
    assert await db.approval_tokens.count_documents({"revokedAt": None}) == 2


# 5: switching to APPROVAL_TOKEN_SECRET keeps JWT-derived links valid
def test_switch_to_explicit_secret_keeps_derived_links(monkeypatch):
    monkeypatch.delenv("APPROVAL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("APPROVAL_TOKEN_SECRET_PREVIOUS", raising=False)
    token, _ = approval_service.create_action_token("a1", "u1", "org1", "approve")
    monkeypatch.setenv("APPROVAL_TOKEN_SECRET", "brand-new-secret")
    assert approval_service.decode_action_token(token).approval_id == "a1"
    new_token, doc = approval_service.create_action_token("a1", "u1", "org1", "approve")
    assert doc["kid"] == approval_service._kid(b"brand-new-secret")


# =========================================================================== final nits (c1174f1)
async def test_decision_email_queue_failure_still_200(http, seeded, sent, db, monkeypatch):
    await _submit(http, seeded)

    async def boom(*a, **k):
        raise RuntimeError("insert failed")
    monkeypatch.setattr(approvals_route, "_new_notification", boom)
    r = await http.post(f"{API_PREFIX}/approvals/email-action", json={"token": _token_from(sent[0]["html"], 0)})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert (await db.contracts.find_one({"id": "c-1"}))["status"] == "approved"


@pytest.mark.parametrize("hops,xfp,expected", [
    ("1", "https", "https"),
    ("1", "http, https", "https"),        # rightmost (proxy-added) wins
    ("1", "javascript", "http"),          # only http/https accepted
    ("2", "https", "http"),               # too few entries -> untrusted
    ("0", "https", "http"),               # XFF/XFP trust disabled
])
async def test_forwarded_proto_redirect(monkeypatch, hops, xfp, expected):
    from utils.proxy import ForwardedProtoMiddleware
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", hops)
    app = FastAPI()

    @app.get("/items/")
    async def items():
        return {}
    app.add_middleware(ForwardedProtoMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        r = await c.get("/items", headers={"x-forwarded-proto": xfp})
    assert r.status_code == 307
    assert r.headers["location"].startswith(f"{expected}://")


async def test_request_log_uses_client_key(caplog, monkeypatch):
    from utils.logging_config import RequestLoggingMiddleware
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request"}

    async def send(_):
        pass

    with caplog.at_level("INFO", logger="lexisense.request"):
        await RequestLoggingMiddleware(app)(
            {"type": "http", "method": "GET", "path": "/p", "query_string": b"", "client": ("10.0.0.1", 1),
             "headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9")]}, receive, send)
    ips = {getattr(r, "client_ip", None) for r in caplog.records} - {None}
    assert ips == {"203.0.113.9"}
