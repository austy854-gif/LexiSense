"""B2 read-only past-due state and the BILLING_UI_ENABLED flag (backend side).

* After the past-due grace period the org is READ-ONLY: contracts can still be
  listed, viewed and downloaded (PDF export); uploads, AI analysis, chat and
  new invitations are blocked with the payment-failed message.
* BILLING_UI_ENABLED (default off) is exposed at runtime via GET
  /billing/config and /billing/subscription; while it is off, blocking
  messages point to support, never to the hidden Billing page.
"""
import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from routes import billing as billing_route
from routes import contracts as contracts_route
from routes import export as export_route
from routes import team as team_route
from utils import feature_flags
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract
# Shared fixtures / states (importing a fixture makes it available here).
from tests.unit.test_billing_integrity import (  # noqa: F401
    CANCELLED,
    EXPIRED_TRIAL,
    GRACE,
    GRACE_OVER,
    PAID,
    agentic_client,
    agentic_seed,
    billing_doc,
    billing_env,
    chat_contract,
    fake_chat,
    queued,
    upload_stubs,
)

BILLING_PAGE_HINTS = ("upgrade", "billing portal", "resubscribe", "checkout", "billing page")


@pytest.fixture
async def http(app, db):
    """contracts + team (conftest) + billing + export routers."""
    billing_route.init_db(db)
    export_route.init_db(db)
    app.include_router(billing_route.router, prefix=API_PREFIX)
    app.include_router(export_route.router, prefix=API_PREFIX)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def seeded(db, org_id, users, owner):
    for u in (users["admin"], owner):
        await db.users.insert_one(dict(u))
    await db.contracts.insert_one(make_contract(org_id, owner["id"], contract_id="c-view", title="Lease"))


def _flag_off(billing_env):
    billing_env.delenv("BILLING_UI_ENABLED", raising=False)


# --------------------------------------------------------------------------
# B2: read-only after the grace period
# --------------------------------------------------------------------------
async def test_read_only_org_can_list_view_and_download(http, db, org_id, owner, seeded):
    await db.organization_billing.insert_one(billing_doc(org_id, **GRACE_OVER))
    h = auth_headers(owner)
    listing = await http.get(f"{API_PREFIX}/contracts", headers=h)
    assert listing.status_code == 200, listing.text
    assert [c["id"] for c in listing.json()] == ["c-view"]
    one = await http.get(f"{API_PREFIX}/contracts/c-view", headers=h)
    assert one.status_code == 200 and one.json()["title"] == "Lease"
    pdf = await http.get(f"{API_PREFIX}/export/contract/c-view/pdf", headers=h)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"


async def test_read_only_org_cannot_upload(http, db, org_id, owner, upload_stubs):
    await db.organization_billing.insert_one(billing_doc(org_id, **GRACE_OVER))
    resp = await http.post(f"{API_PREFIX}/contracts", data={"title": "NDA"},
                           files={"file": ("nda.txt", b"hello", "text/plain")}, headers=auth_headers(owner))
    assert resp.status_code in (402, 403), resp.text
    assert "payment failed" in resp.json()["detail"] and "read-only" in resp.json()["detail"]
    bulk = await http.post(f"{API_PREFIX}/contracts/bulk", files=[("files", ("a.txt", b"x", "text/plain"))],
                           headers=auth_headers(owner))
    assert await db.contracts.count_documents({}) == 0
    assert "payment failed" in bulk.text


async def test_read_only_org_cannot_chat(http, db, org_id, owner, chat_contract, fake_chat):
    await db.organization_billing.insert_one(billing_doc(org_id, **GRACE_OVER))
    resp = await http.post(f"{API_PREFIX}/contracts/c-chat/chat", json={"question": "Q"}, headers=auth_headers(owner))
    assert resp.status_code == 403 and "payment failed" in resp.json()["detail"]
    assert fake_chat == []


async def test_read_only_org_cannot_run_ai_analysis(agentic_client, agentic_seed, queued, db, org_id, owner):
    await db.organization_billing.insert_one(billing_doc(org_id, **GRACE_OVER))
    resp = await agentic_client.post(f"{API_PREFIX}/agentic/redline/analyze",
                                     json={"contractId": "c-1", "playbookId": "pb-1"}, headers=auth_headers(owner))
    assert resp.status_code == 403 and "payment failed" in resp.json()["detail"]
    assert queued == []


async def _invite(http, admin, email="new@example.com"):
    return await http.post(f"{API_PREFIX}/team/invite", json={"email": email, "role": "user"},
                           headers=auth_headers(admin))


async def test_read_only_org_cannot_invite(http, db, org_id, users, seeded):
    await db.organization_billing.insert_one(billing_doc(org_id, **GRACE_OVER))
    resp = await _invite(http, users["admin"])
    assert resp.status_code == 403
    assert "payment failed" in resp.json()["detail"]
    assert await db.invitations.count_documents({}) == 0


@pytest.mark.parametrize("state", [GRACE, PAID, {}, EXPIRED_TRIAL, CANCELLED])
async def test_invites_only_blocked_when_read_only(http, db, org_id, users, seeded, state):
    """Grace period, paid and trial orgs invite as before; trial-expired and
    cancelled orgs keep today's behaviour too (only read-only is new)."""
    await db.organization_billing.insert_one(billing_doc(org_id, **state))
    resp = await _invite(http, users["admin"])
    assert resp.status_code == 200, resp.text


async def test_read_only_is_scoped_to_the_callers_org(http, db, org_id, other_org_id, users, seeded):
    await db.organization_billing.insert_one(billing_doc(other_org_id, **GRACE_OVER))
    await db.organization_billing.insert_one(billing_doc(org_id, **PAID))
    assert (await _invite(http, users["admin"])).status_code == 200


async def test_subscription_reports_read_only(http, db, org_id, owner):
    await db.organization_billing.insert_one(billing_doc(org_id, **GRACE_OVER))
    body = (await http.get(f"{API_PREFIX}/billing/subscription", headers=auth_headers(owner))).json()
    assert body["readOnly"] is True and body["accessState"] == "past_due"
    await db.organization_billing.update_one({"organizationId": org_id}, {"$set": GRACE})
    body = (await http.get(f"{API_PREFIX}/billing/subscription", headers=auth_headers(owner))).json()
    assert body["readOnly"] is False and body["accessState"] == "past_due_grace"


def test_read_only_only_for_past_due_after_grace():
    ce = billing_route.compute_entitlement
    assert ce(billing_doc("o", **GRACE_OVER)).read_only
    for state in (GRACE, PAID, {}, EXPIRED_TRIAL, CANCELLED):
        assert not ce(billing_doc("o", **state)).read_only


# --------------------------------------------------------------------------
# BILLING_UI_ENABLED
# --------------------------------------------------------------------------
def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("BILLING_UI_ENABLED", raising=False)
    assert feature_flags.billing_ui_enabled() is False
    for value, expected in (("true", True), ("1", True), ("on", True), ("false", False), ("", False)):
        monkeypatch.setenv("BILLING_UI_ENABLED", value)
        assert feature_flags.billing_ui_enabled() is expected


async def test_config_endpoint_exposes_flag_at_runtime(http, billing_env):
    _flag_off(billing_env)
    resp = await http.get(f"{API_PREFIX}/billing/config")  # no auth needed
    assert resp.status_code == 200 and resp.json() == {"billingUiEnabled": False}
    billing_env.setenv("BILLING_UI_ENABLED", "true")
    assert (await http.get(f"{API_PREFIX}/billing/config")).json() == {"billingUiEnabled": True}


async def test_subscription_carries_flag(http, db, org_id, owner, billing_env):
    _flag_off(billing_env)
    body = (await http.get(f"{API_PREFIX}/billing/subscription", headers=auth_headers(owner))).json()
    assert body["billingUiEnabled"] is False
    billing_env.setenv("BILLING_UI_ENABLED", "yes")
    body = (await http.get(f"{API_PREFIX}/billing/subscription", headers=auth_headers(owner))).json()
    assert body["billingUiEnabled"] is True


@pytest.mark.parametrize("state", [EXPIRED_TRIAL, CANCELLED, GRACE_OVER])
def test_flag_off_blocking_messages_point_to_support(billing_env, state):
    _flag_off(billing_env)
    msg = billing_route.compute_entitlement(billing_doc("o", **state)).message
    assert "contact support" in msg.lower()
    assert not any(h in msg.lower() for h in BILLING_PAGE_HINTS), msg


@pytest.mark.parametrize("state,hint", [(EXPIRED_TRIAL, "upgrade"), (CANCELLED, "resubscribe"),
                                        (GRACE_OVER, "billing portal")])
def test_flag_on_blocking_messages_point_to_billing(billing_env, state, hint):
    msg = billing_route.compute_entitlement(billing_doc("o", **state)).message
    assert hint in msg.lower()


def test_flag_off_grace_warning_points_to_support(billing_env):
    _flag_off(billing_env)
    warning = billing_route.compute_entitlement(billing_doc("o", **GRACE)).warning
    assert "payment failed" in warning and "contact support" in warning
    assert not any(h in warning.lower() for h in BILLING_PAGE_HINTS)


async def test_flag_off_trial_limit_message_points_to_support(db, org_id, billing_env):
    _flag_off(billing_env)
    billing_route.init_db(db)
    await db.organization_billing.insert_one(billing_doc(org_id, trialContractsUsed=3))
    allowed, msg = await billing_route.reserve_trial_upload_atomic(org_id)
    assert allowed is False and "trial limit of 3" in msg and "contact support" in msg
    assert "upgrade" not in msg.lower()
    billing_env.setenv("BILLING_UI_ENABLED", "true")
    allowed, msg = await billing_route.reserve_trial_upload_atomic(org_id)
    assert allowed is False and "upgrade" in msg.lower()


async def test_flag_off_upload_rejection_has_no_billing_page_hint(http, db, org_id, owner, upload_stubs, billing_env):
    _flag_off(billing_env)
    await db.organization_billing.insert_one(billing_doc(org_id, **EXPIRED_TRIAL))
    resp = await http.post(f"{API_PREFIX}/contracts", data={"title": "NDA"},
                           files={"file": ("nda.txt", b"hello", "text/plain")}, headers=auth_headers(owner))
    assert resp.status_code in (402, 403)
    detail = resp.json()["detail"].lower()
    assert "contact support" in detail and not any(h in detail for h in BILLING_PAGE_HINTS)


async def test_flag_off_does_not_change_enforcement(db, billing_env):
    """The flag only changes what the UI shows; entitlements are identical."""
    def snapshot():
        return [
            (e.state, e.can_use_ai, e.unlimited_uploads, e.read_only)
            for e in (billing_route.compute_entitlement(billing_doc("o", **s))
                      for s in (GRACE_OVER, GRACE, PAID, {}, EXPIRED_TRIAL, CANCELLED))
        ]
    on = snapshot()
    _flag_off(billing_env)
    assert snapshot() == on
