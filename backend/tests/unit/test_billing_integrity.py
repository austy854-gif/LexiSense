"""Regression tests for the billing / entitlement integrity fixes.

Covers: server-side Stripe price allow-list, no second Stripe trial at
checkout, AI entitlement gate on chat / playbook / agent endpoints, paid
entitlement while Stripe is ``trialing``, correct past-due status reporting,
the plan member cap, viewer upload block, friendly Stripe errors and the
single env-driven plan config.

No network: every Stripe call is replaced by a fake.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import stripe
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

import services.celery_app as celery_module
from models.billing import BillingStatus, SubscriptionTier
from routes import agentic as agentic_route
from routes import billing as billing_route
from routes import contracts as contracts_route
from utils import plans as plans_module
from tests.unit.conftest import (
    API_PREFIX,
    auth_headers,
    iso_in,
    make_contract,
    make_invitation,
    make_user,
)

PRICE_MONTHLY = "price_team_monthly"
PRICE_ANNUAL = "price_team_annual"

BILLING_ENV = (
    "BILLING_TRIAL_DAYS",
    "BILLING_TRIAL_UPLOAD_LIMIT",
    "BILLING_TRIAL_MAX_MEMBERS",
    "BILLING_TEAM_MAX_MEMBERS",
    "BILLING_TEAM_PRICE_MONTHLY",
    "BILLING_PAST_DUE_GRACE_DAYS",
    "STRIPE_TEAM_PRICE_ID",
    "STRIPE_TEAM_PRICE_IDS",
)


@pytest.fixture(autouse=True)
def billing_env(monkeypatch):
    for name in BILLING_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STRIPE_TEAM_PRICE_ID", PRICE_MONTHLY)
    monkeypatch.setenv("STRIPE_TEAM_PRICE_IDS", PRICE_ANNUAL)
    return monkeypatch


def billing_doc(org_id, **overrides):
    doc = {
        "id": f"billing-{org_id}",
        "organizationId": org_id,
        "subscriptionTier": SubscriptionTier.TRIAL,
        "billingStatus": BillingStatus.TRIALING,
        "trialEndDate": iso_in(5),
        "trialContractsUsed": 0,
        "trialContractsLimit": 3,
    }
    doc.update(overrides)
    return doc


PAID = dict(subscriptionTier=SubscriptionTier.TEAM, billingStatus=BillingStatus.ACTIVE,
            stripePriceId=PRICE_MONTHLY, stripeCustomerId="cus_1", trialEndDate=None)
STRIPE_TRIALING = dict(subscriptionTier=SubscriptionTier.TEAM, billingStatus=BillingStatus.TRIALING,
                       stripePriceId=PRICE_MONTHLY, stripeCustomerId="cus_1", trialEndDate=iso_in(6))
PAST_DUE = dict(subscriptionTier=SubscriptionTier.TEAM, billingStatus=BillingStatus.PAST_DUE,
                stripePriceId=PRICE_MONTHLY, stripeCustomerId="cus_1", trialEndDate=None)
CANCELLED = dict(subscriptionTier=SubscriptionTier.CANCELLED, billingStatus=BillingStatus.CANCELLED,
                 trialEndDate=iso_in(-30))
EXPIRED_TRIAL = dict(trialEndDate=iso_in(-1))


# --------------------------------------------------------------------------
# App / client fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def full_app(app, db):
    """conftest app (contracts + team) plus the billing router."""
    billing_route.init_db(db)
    app.include_router(billing_route.router, prefix=API_PREFIX)
    return app


@pytest.fixture
async def http(full_app):
    async with AsyncClient(transport=ASGITransport(app=full_app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def fake_stripe(monkeypatch):
    """Replace every Stripe call used by checkout/portal with a recorder."""
    calls = {"customer": [], "checkout": [], "portal": []}

    def customer_create(**kwargs):
        calls["customer"].append(kwargs)
        return SimpleNamespace(id="cus_new")

    def checkout_create(**kwargs):
        calls["checkout"].append(kwargs)
        return SimpleNamespace(id="cs_test", url="https://checkout.stripe.test/cs_test")

    def portal_create(**kwargs):
        calls["portal"].append(kwargs)
        return SimpleNamespace(url="https://billing.stripe.test/p")

    monkeypatch.setattr(billing_route, "stripe_api_key", "sk_test_fake")
    monkeypatch.setattr(stripe.Customer, "create", staticmethod(customer_create))
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(checkout_create))
    monkeypatch.setattr(stripe.billing_portal.Session, "create", staticmethod(portal_create))
    return calls


CHECKOUT_URLS = {"successUrl": "https://app.test/ok", "cancelUrl": "https://app.test/cancel"}


# --------------------------------------------------------------------------
# Plan config (single source of truth, env-driven)
# --------------------------------------------------------------------------
def test_plan_config_defaults_match_current_pricing():
    trial = plans_module.get_trial_config()
    team = plans_module.get_paid_plans()["team"]
    assert (trial.days, trial.upload_limit, trial.max_members) == (7, 3, 3)
    assert team.max_members == 10 and team.price_monthly == 49 and team.max_uploads is None
    assert team.stripe_price_ids == (PRICE_MONTHLY, PRICE_ANNUAL)
    assert team.default_price_id == PRICE_MONTHLY


def test_plan_config_env_overrides(billing_env):
    billing_env.setenv("BILLING_TRIAL_DAYS", "14")
    billing_env.setenv("BILLING_TRIAL_UPLOAD_LIMIT", "25")
    billing_env.setenv("BILLING_TEAM_MAX_MEMBERS", "15")
    billing_env.setenv("BILLING_TEAM_PRICE_MONTHLY", "129")
    trial = plans_module.get_trial_config()
    team = plans_module.get_paid_plans()["team"]
    assert (trial.days, trial.upload_limit) == (14, 25)
    assert trial.max_members == 3  # trial cap is independent of the team cap
    assert team.max_members == 15 and team.price_monthly == 129


def test_plan_config_invalid_values_fall_back_and_zero_is_unlimited(billing_env):
    billing_env.setenv("BILLING_TRIAL_DAYS", "fourteen")
    billing_env.setenv("BILLING_TRIAL_UPLOAD_LIMIT", "-5")
    billing_env.setenv("BILLING_TEAM_MAX_MEMBERS", "0")
    billing_env.setenv("BILLING_TRIAL_MAX_MEMBERS", "2")
    trial = plans_module.get_trial_config()
    assert trial.days == 7 and trial.upload_limit == 3 and trial.max_members == 2
    assert plans_module.get_paid_plans()["team"].max_members is None


def test_plan_for_price_id_is_an_allowlist():
    assert plans_module.plan_for_price_id(PRICE_ANNUAL).id == "team"
    assert plans_module.plan_for_price_id("price_attacker") is None
    assert plans_module.plan_for_price_id(None) is None


async def test_plans_endpoint_renders_from_config(http, billing_env):
    billing_env.setenv("BILLING_TRIAL_DAYS", "14")
    billing_env.setenv("BILLING_TRIAL_UPLOAD_LIMIT", "25")
    billing_env.setenv("BILLING_TEAM_MAX_MEMBERS", "15")
    billing_env.setenv("BILLING_TEAM_PRICE_MONTHLY", "129")
    body = (await http.get(f"{API_PREFIX}/billing/plans")).json()
    trial, team = body["plans"]
    assert "14-day free trial" in trial["features"]
    assert "Up to 25 contract uploads" in trial["features"]
    assert team["price"] == 129 and team["priceId"] == PRICE_MONTHLY
    assert "Team collaboration (up to 15 members)" in team["features"]


# --------------------------------------------------------------------------
# P1: server-side price allow-list + P1: no second Stripe trial
# --------------------------------------------------------------------------
async def test_checkout_rejects_unlisted_price(http, db, users, fake_stripe):
    resp = await http.post(
        f"{API_PREFIX}/billing/checkout",
        json={"priceId": "price_one_cent", **CHECKOUT_URLS},
        headers=auth_headers(users["admin"]),
    )
    assert resp.status_code == 400
    assert fake_stripe["checkout"] == [] and fake_stripe["customer"] == []


@pytest.mark.parametrize("price", [PRICE_MONTHLY, PRICE_ANNUAL])
async def test_checkout_accepts_allowlisted_price_without_stripe_trial(http, users, fake_stripe, price):
    resp = await http.post(
        f"{API_PREFIX}/billing/checkout",
        json={"priceId": price, **CHECKOUT_URLS},
        headers=auth_headers(users["admin"]),
    )
    assert resp.status_code == 200, resp.text
    (call,) = fake_stripe["checkout"]
    assert call["line_items"] == [{"price": price, "quantity": 1}]
    assert "trial_period_days" not in call["subscription_data"]
    assert "trial_end" not in call["subscription_data"]
    assert call["subscription_data"]["metadata"] == {"organizationId": users["admin"]["organizationId"], "planId": "team"}


async def test_checkout_without_price_uses_default_plan_price(http, users, fake_stripe):
    resp = await http.post(f"{API_PREFIX}/billing/checkout", json=CHECKOUT_URLS, headers=auth_headers(users["admin"]))
    assert resp.status_code == 200
    assert fake_stripe["checkout"][0]["line_items"][0]["price"] == PRICE_MONTHLY


async def test_checkout_plan_id_resolves_and_mismatch_rejected(http, users, fake_stripe):
    ok = await http.post(f"{API_PREFIX}/billing/checkout", json={"planId": "team", **CHECKOUT_URLS},
                         headers=auth_headers(users["admin"]))
    assert ok.status_code == 200
    bad_plan = await http.post(f"{API_PREFIX}/billing/checkout", json={"planId": "enterprise", **CHECKOUT_URLS},
                               headers=auth_headers(users["admin"]))
    assert bad_plan.status_code == 400
    mismatch = await http.post(f"{API_PREFIX}/billing/checkout",
                               json={"planId": "enterprise", "priceId": PRICE_MONTHLY, **CHECKOUT_URLS},
                               headers=auth_headers(users["admin"]))
    assert mismatch.status_code == 400
    assert len(fake_stripe["checkout"]) == 1


async def test_checkout_unconfigured_price_is_503(http, users, fake_stripe, billing_env):
    billing_env.delenv("STRIPE_TEAM_PRICE_ID")
    billing_env.delenv("STRIPE_TEAM_PRICE_IDS")
    resp = await http.post(f"{API_PREFIX}/billing/checkout", json=CHECKOUT_URLS, headers=auth_headers(users["admin"]))
    assert resp.status_code == 503
    assert fake_stripe["checkout"] == []


@pytest.mark.parametrize("role", ["viewer", "user", "manager"])
async def test_checkout_still_admin_only(http, users, fake_stripe, role):
    resp = await http.post(f"{API_PREFIX}/billing/checkout", json=CHECKOUT_URLS, headers=auth_headers(users[role]))
    assert resp.status_code == 403
    assert fake_stripe["checkout"] == []


# --------------------------------------------------------------------------
# Friendly Stripe errors (no raw Stripe text to users)
# --------------------------------------------------------------------------
RAW_STRIPE_TEXT = "No such price: 'price_x'; a similar object exists in live mode (req_abc123)"


async def test_checkout_stripe_error_is_friendly_and_logged(http, db, users, fake_stripe, monkeypatch, caplog):
    def boom(**kwargs):
        raise stripe.InvalidRequestError(RAW_STRIPE_TEXT, param="price")

    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(boom))
    with caplog.at_level("ERROR", logger="routes.billing"):
        resp = await http.post(f"{API_PREFIX}/billing/checkout", json=CHECKOUT_URLS,
                               headers=auth_headers(users["admin"]))
    assert resp.status_code == 502
    assert resp.json()["detail"] == billing_route.STRIPE_CHECKOUT_ERROR_MESSAGE
    assert "price_x" not in resp.text and "req_abc123" not in resp.text
    assert "price_x" in caplog.text


async def test_customer_create_stripe_error_is_friendly(http, users, fake_stripe, monkeypatch):
    def boom(**kwargs):
        raise stripe.APIConnectionError(RAW_STRIPE_TEXT)

    monkeypatch.setattr(stripe.Customer, "create", staticmethod(boom))
    resp = await http.post(f"{API_PREFIX}/billing/checkout", json=CHECKOUT_URLS,
                           headers=auth_headers(users["admin"]))
    assert resp.status_code == 502
    assert resp.json()["detail"] == billing_route.STRIPE_CHECKOUT_ERROR_MESSAGE


async def test_portal_stripe_error_is_friendly(http, db, users, org_id, fake_stripe, monkeypatch):
    await db.organization_billing.insert_one(billing_doc(org_id, **PAID))

    def boom(**kwargs):
        raise stripe.AuthenticationError(RAW_STRIPE_TEXT)

    monkeypatch.setattr(stripe.billing_portal.Session, "create", staticmethod(boom))
    resp = await http.post(f"{API_PREFIX}/billing/portal", json={"returnUrl": "https://app.test/b"},
                           headers=auth_headers(users["admin"]))
    assert resp.status_code == 502
    assert resp.json()["detail"] == billing_route.STRIPE_PORTAL_ERROR_MESSAGE
    assert "req_abc123" not in resp.text


# --------------------------------------------------------------------------
# P1: AI entitlement gate (chat, playbook analysis, agent trigger)
# --------------------------------------------------------------------------
@pytest.fixture
def fake_chat(monkeypatch):
    calls = []

    async def _chat(text, question, session_id):
        calls.append(question)
        return "answer"

    monkeypatch.setattr(contracts_route, "get_chat_response", _chat)
    return calls


@pytest.fixture
async def chat_contract(db, org_id, owner):
    await db.contracts.insert_one(make_contract(org_id, owner["id"], contract_id="c-chat", originalText="Term: 1 year."))


@pytest.mark.parametrize("state", [EXPIRED_TRIAL, CANCELLED, PAST_DUE])
async def test_chat_blocked_without_entitlement(http, db, org_id, owner, chat_contract, fake_chat, state):
    await db.organization_billing.insert_one(billing_doc(org_id, **state))
    resp = await http.post(f"{API_PREFIX}/contracts/c-chat/chat", json={"question": "Term?"},
                           headers=auth_headers(owner))
    assert resp.status_code == 403
    assert fake_chat == []


@pytest.mark.parametrize("state", [{}, PAID, STRIPE_TRIALING])
async def test_chat_allowed_with_entitlement(http, db, org_id, owner, chat_contract, fake_chat, state):
    await db.organization_billing.insert_one(billing_doc(org_id, **state))
    resp = await http.post(f"{API_PREFIX}/contracts/c-chat/chat", json={"question": "Term?"},
                           headers=auth_headers(owner))
    assert resp.status_code == 200, resp.text
    assert fake_chat == ["Term?"]


async def test_chat_gate_uses_callers_org_only(http, db, org_id, other_org_id, chat_contract, fake_chat):
    """A paid org can't unlock chat for another org, and cross-org stays hidden."""
    await db.organization_billing.insert_one(billing_doc(org_id, **EXPIRED_TRIAL))
    await db.organization_billing.insert_one(billing_doc(other_org_id, **PAID))
    outsider = make_user(other_org_id, role="admin", user_id="outsider")
    resp = await http.post(f"{API_PREFIX}/contracts/c-chat/chat", json={"question": "Term?"},
                           headers=auth_headers(outsider))
    assert resp.status_code == 404
    assert fake_chat == []


@pytest.fixture
def queued(monkeypatch):
    calls = []

    async def fake_threadpool(fn, *args, **kwargs):
        calls.append(fn)
        return SimpleNamespace(id="task-1")

    monkeypatch.setattr(celery_module, "CELERY_BROKER_URL", "redis://broker:6379/0")
    monkeypatch.setattr(agentic_route, "run_in_threadpool", fake_threadpool)
    return calls


@pytest.fixture
async def agentic_client(db):
    agentic_route.init_db(db)
    billing_route.init_db(db)
    app = FastAPI()
    api = APIRouter(prefix=API_PREFIX)
    api.include_router(agentic_route.build_router({"redline", "agents"}))
    app.include_router(api)
    # raise_app_exceptions=False: see test_agent_trigger_gated (pre-existing
    # response-model bug on main makes a successful trigger return 500).
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def agentic_seed(db, org_id, owner):
    await db.contracts.insert_one(make_contract(org_id, owner["id"], contract_id="c-1"))
    await db.legal_playbooks.insert_one({"id": "pb-1", "organizationId": org_id, "rules": []})
    await db.contract_agents.insert_one({"id": "ag-1", "organizationId": org_id})


@pytest.mark.parametrize("state,expected", [(EXPIRED_TRIAL, 403), (CANCELLED, 403), (PAST_DUE, 403),
                                             ({}, 200), (PAID, 200), (STRIPE_TRIALING, 200)])
async def test_playbook_analysis_gated(agentic_client, agentic_seed, queued, db, org_id, owner, state, expected):
    await db.organization_billing.insert_one(billing_doc(org_id, **state))
    resp = await agentic_client.post(f"{API_PREFIX}/agentic/redline/analyze",
                                     json={"contractId": "c-1", "playbookId": "pb-1"},
                                     headers=auth_headers(owner))
    assert resp.status_code == expected, resp.text
    assert len(queued) == (1 if expected == 200 else 0)


@pytest.mark.parametrize("state,allowed", [(EXPIRED_TRIAL, False), (PAID, True)])
async def test_agent_trigger_gated(agentic_client, agentic_seed, queued, db, org_id, users, state, allowed):
    await db.organization_billing.insert_one(billing_doc(org_id, **state))
    resp = await agentic_client.post(f"{API_PREFIX}/agentic/agents/ag-1/trigger", headers=auth_headers(users["admin"]))
    if allowed:
        # Gate passed and the run was queued. (The handler then fails building
        # AgentRunResponse(status="queued") -- a pre-existing bug on main,
        # unrelated to billing, so only "not 403" is asserted here.)
        assert resp.status_code != 403
        assert len(queued) == 1
    else:
        assert resp.status_code == 403
        assert queued == []


# --------------------------------------------------------------------------
# P2: entitlement follows paid/subscribed state
# --------------------------------------------------------------------------
async def test_stripe_trialing_customer_has_unlimited_uploads(db, org_id):
    billing_route.init_db(db)
    await db.organization_billing.insert_one(billing_doc(org_id, trialContractsUsed=3, **STRIPE_TRIALING))
    for _ in range(5):
        assert await billing_route.reserve_trial_upload_atomic(org_id) == (True, "")
    doc = await db.organization_billing.find_one({"organizationId": org_id})
    assert doc["trialContractsUsed"] == 3  # paid uploads never consume trial quota
    await billing_route.release_trial_upload(org_id)
    assert (await db.organization_billing.find_one({"organizationId": org_id}))["trialContractsUsed"] == 3


async def test_trial_cap_comes_from_config(db, org_id, billing_env):
    billing_route.init_db(db)
    billing_env.setenv("BILLING_TRIAL_UPLOAD_LIMIT", "5")
    await db.organization_billing.insert_one(billing_doc(org_id, trialContractsUsed=3, trialContractsLimit=3))
    assert (await billing_route.reserve_trial_upload_atomic(org_id))[0] is True
    assert (await billing_route.reserve_trial_upload_atomic(org_id))[0] is True
    ok, msg = await billing_route.reserve_trial_upload_atomic(org_id)
    assert ok is False and "5 contract uploads" in msg


async def test_new_trial_uses_configured_length_and_limit(db, org_id, billing_env):
    billing_route.init_db(db)
    billing_env.setenv("BILLING_TRIAL_DAYS", "14")
    billing_env.setenv("BILLING_TRIAL_UPLOAD_LIMIT", "25")
    billing = await billing_route.get_or_create_billing(org_id)
    end = datetime.fromisoformat(billing.trialEndDate)
    assert timedelta(days=13, hours=23) < end - datetime.now(timezone.utc) <= timedelta(days=14)
    assert billing.trialContractsLimit == 25


@pytest.mark.parametrize("state,ok,fragment", [
    (EXPIRED_TRIAL, False, "trial has ended"),
    (PAST_DUE, False, "payment failed"),
    (CANCELLED, False, "cancelled"),
    (PAID, True, ""),
])
async def test_upload_reservation_messages(db, org_id, state, ok, fragment):
    billing_route.init_db(db)
    await db.organization_billing.insert_one(billing_doc(org_id, **state))
    allowed, msg = await billing_route.reserve_trial_upload_atomic(org_id)
    assert allowed is ok and fragment in msg


def test_compute_entitlement_member_limits(billing_env):
    billing_env.setenv("BILLING_TEAM_MAX_MEMBERS", "12")
    billing_env.setenv("BILLING_TRIAL_MAX_MEMBERS", "3")
    assert billing_route.compute_entitlement(billing_doc("o", **PAID)).member_limit == 12
    assert billing_route.compute_entitlement(billing_doc("o")).member_limit == 3


# --------------------------------------------------------------------------
# P2: past-due is reported as past-due (not trial); webhook handling
# --------------------------------------------------------------------------
def _subscription(status, price=PRICE_MONTHLY, trial_end=None, sub_id="sub_1"):
    now = int(datetime.now(timezone.utc).timestamp())
    sub = {
        "id": sub_id,
        "status": status,
        "customer": "cus_1",
        "metadata": {"organizationId": "org-alpha"},
        "items": {"data": [{"price": {"id": price}}]},
        "current_period_start": now,
        "current_period_end": now + 30 * 86400,
        "cancel_at_period_end": False,
    }
    if trial_end:
        sub["trial_end"] = trial_end
    return sub


@pytest.mark.parametrize("stripe_status", ["past_due", "unpaid"])
async def test_past_due_reported_as_team_past_due(http, db, org_id, owner, stripe_status):
    await db.organization_billing.insert_one(billing_doc(org_id, **PAID))
    await billing_route.update_billing_from_subscription(org_id, _subscription(stripe_status), "cus_1")
    body = (await http.get(f"{API_PREFIX}/billing/subscription", headers=auth_headers(owner))).json()
    assert body["subscriptionTier"] == "team"
    assert body["billingStatus"] == "past_due"


async def test_active_subscription_sets_plan_and_keeps_in_app_trial_end(db, org_id):
    billing_route.init_db(db)
    original_end = iso_in(3)
    await db.organization_billing.insert_one(billing_doc(org_id, trialEndDate=original_end))
    await billing_route.update_billing_from_subscription(org_id, _subscription("active", price=PRICE_ANNUAL), "cus_1")
    doc = await db.organization_billing.find_one({"organizationId": org_id})
    assert doc["subscriptionTier"] == "team" and doc["billingStatus"] == "active"
    assert doc["planId"] == "team" and doc["stripePriceId"] == PRICE_ANNUAL
    assert doc["trialEndDate"] == original_end  # not wiped to None


async def test_incomplete_subscription_keeps_trial_expiry(db, org_id):
    """An abandoned checkout must not turn the trial into an open-ended one."""
    billing_route.init_db(db)
    await db.organization_billing.insert_one(billing_doc(org_id, **EXPIRED_TRIAL))
    await billing_route.update_billing_from_subscription(org_id, _subscription("incomplete"), "cus_1")
    doc = await db.organization_billing.find_one({"organizationId": org_id})
    assert doc["subscriptionTier"] == "trial"
    assert billing_route.compute_entitlement(doc).state == billing_route.ACCESS_TRIAL_EXPIRED


async def test_legacy_stripe_trial_still_records_trial_end(db, org_id):
    billing_route.init_db(db)
    await db.organization_billing.insert_one(billing_doc(org_id))
    end = int((datetime.now(timezone.utc) + timedelta(days=4)).timestamp())
    await billing_route.update_billing_from_subscription(org_id, _subscription("trialing", trial_end=end), "cus_1")
    doc = await db.organization_billing.find_one({"organizationId": org_id})
    assert doc["trialEndDate"] == datetime.fromtimestamp(end, tz=timezone.utc).isoformat()
    assert billing_route.compute_entitlement(doc).is_paid


async def test_unknown_price_falls_back_to_default_plan(db, org_id):
    billing_route.init_db(db)
    await db.organization_billing.insert_one(billing_doc(org_id))
    await billing_route.update_billing_from_subscription(org_id, _subscription("active", price="price_legacy"), "cus_1")
    doc = await db.organization_billing.find_one({"organizationId": org_id})
    assert doc["planId"] is None
    ent = billing_route.compute_entitlement(doc)
    assert ent.is_paid and ent.plan_id == "team" and ent.member_limit == 10


async def test_invoice_paid_restores_team_tier(db, org_id):
    billing_route.init_db(db)
    # A record written by the old code: past_due relabelled as trial.
    await db.organization_billing.insert_one(billing_doc(
        org_id, subscriptionTier=SubscriptionTier.TRIAL, billingStatus=BillingStatus.PAST_DUE,
        stripeCustomerId="cus_1", trialEndDate=None))
    await billing_route.handle_invoice_paid({"customer": "cus_1"})
    doc = await db.organization_billing.find_one({"organizationId": org_id})
    assert (doc["subscriptionTier"], doc["billingStatus"]) == ("team", "active")


async def test_payment_failed_marks_subscriber_past_due_only(db, org_id, other_org_id):
    billing_route.init_db(db)
    await db.organization_billing.insert_one(billing_doc(org_id, **PAID))
    await db.organization_billing.insert_one(billing_doc(other_org_id, stripeCustomerId="cus_2"))
    await billing_route.handle_payment_failed({"customer": "cus_1"})
    await billing_route.handle_payment_failed({"customer": "cus_2"})
    await billing_route.handle_payment_failed({"customer": None})
    assert (await db.organization_billing.find_one({"organizationId": org_id}))["billingStatus"] == "past_due"
    trial = await db.organization_billing.find_one({"organizationId": other_org_id})
    assert (trial["subscriptionTier"], trial["billingStatus"]) == ("trial", "trialing")


async def test_webhook_still_requires_valid_signature(http, monkeypatch):
    monkeypatch.setattr(billing_route, "stripe_api_key", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    resp = await http.post(f"{API_PREFIX}/billing/webhook", content=b'{"type":"invoice.paid"}',
                           headers={"stripe-signature": "t=1,v1=bad"})
    assert resp.status_code == 400


async def test_webhook_dispatches_subscription_update(http, db, org_id, monkeypatch):
    monkeypatch.setattr(billing_route, "stripe_api_key", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    await db.organization_billing.insert_one(billing_doc(org_id, **PAID))
    event = {"type": "customer.subscription.updated", "data": {"object": _subscription("past_due")}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", staticmethod(lambda p, s, k: event))
    resp = await http.post(f"{API_PREFIX}/billing/webhook", content=b"{}", headers={"stripe-signature": "sig"})
    assert resp.status_code == 200
    doc = await db.organization_billing.find_one({"organizationId": org_id})
    assert (doc["subscriptionTier"], doc["billingStatus"]) == ("team", "past_due")


# --------------------------------------------------------------------------
# P2: team member cap
# --------------------------------------------------------------------------
async def _invite(client, admin, email):
    return await client.post(f"{API_PREFIX}/team/invite", json={"email": email, "role": "user"},
                             headers=auth_headers(admin))


async def test_invite_blocked_at_member_cap(http, db, org_id, users, billing_env):
    billing_env.setenv("BILLING_TEAM_MAX_MEMBERS", "3")
    await db.organization_billing.insert_one(billing_doc(org_id, **PAID))
    await db.users.insert_one(dict(users["admin"]))
    await db.users.insert_one(dict(users["user"]))
    assert (await _invite(http, users["admin"], "third@example.com")).status_code == 200
    resp = await _invite(http, users["admin"], "fourth@example.com")
    assert resp.status_code == 403
    assert "up to 3 team members" in resp.json()["detail"]
    assert await db.invitations.count_documents({"email": "fourth@example.com"}) == 0


async def test_cap_ignores_expired_invites_inactive_users_and_other_orgs(http, db, org_id, other_org_id, users, billing_env):
    billing_env.setenv("BILLING_TEAM_MAX_MEMBERS", "2")
    await db.organization_billing.insert_one(billing_doc(org_id, **PAID))
    await db.users.insert_one(dict(users["admin"]))
    inactive = make_user(org_id, user_id="gone")
    inactive["isActive"] = False
    await db.users.insert_one(inactive)
    for i in range(3):
        await db.users.insert_one(make_user(other_org_id, user_id=f"other-{i}"))
    await db.invitations.insert_one(make_invitation(org_id, users["admin"]["id"], expires_at=iso_in(-1),
                                                    email="stale@example.com"))
    await db.invitations.insert_one(make_invitation(other_org_id, "x", email="theirs@example.com"))
    assert (await _invite(http, users["admin"], "new@example.com")).status_code == 200
    assert (await _invite(http, users["admin"], "over@example.com")).status_code == 403


async def test_trial_org_uses_trial_member_cap(http, db, org_id, users, billing_env):
    billing_env.setenv("BILLING_TRIAL_MAX_MEMBERS", "1")
    await db.organization_billing.insert_one(billing_doc(org_id))
    await db.users.insert_one(dict(users["admin"]))
    assert (await _invite(http, users["admin"], "a@example.com")).status_code == 403


async def test_trial_member_cap_defaults_to_three(http, db, org_id, users):
    await db.organization_billing.insert_one(billing_doc(org_id))
    await db.users.insert_one(dict(users["admin"]))
    await db.users.insert_one(dict(users["user"]))
    assert (await _invite(http, users["admin"], "third@example.com")).status_code == 200
    resp = await _invite(http, users["admin"], "fourth@example.com")
    assert resp.status_code == 403
    assert "up to 3 team members" in resp.json()["detail"]


async def test_trial_member_cap_is_config_driven(http, db, org_id, users, billing_env):
    billing_env.setenv("BILLING_TRIAL_MAX_MEMBERS", "0")  # 0 = unlimited
    await db.organization_billing.insert_one(billing_doc(org_id))
    for i in range(5):
        await db.users.insert_one(make_user(org_id, user_id=f"t-{i}"))
    assert (await _invite(http, users["admin"], "sixth@example.com")).status_code == 200
    billing_env.setenv("BILLING_TRIAL_MAX_MEMBERS", "6")
    assert (await _invite(http, users["admin"], "seventh@example.com")).status_code == 403


def test_trial_cap_does_not_follow_team_cap(billing_env):
    billing_env.setenv("BILLING_TEAM_MAX_MEMBERS", "25")
    assert plans_module.get_trial_config().max_members == 3
    assert billing_route.compute_entitlement(billing_doc("o")).member_limit == 3
    assert billing_route.compute_entitlement(billing_doc("o", **PAID)).member_limit == 25


async def test_default_cap_is_ten(http, db, org_id, users):
    await db.organization_billing.insert_one(billing_doc(org_id, **PAID))
    for i in range(10):
        await db.users.insert_one(make_user(org_id, user_id=f"m-{i}"))
    resp = await _invite(http, users["admin"], "eleventh@example.com")
    assert resp.status_code == 403


async def test_accept_invite_blocked_when_team_full(http, db, org_id, users, billing_env):
    billing_env.setenv("BILLING_TEAM_MAX_MEMBERS", "2")
    await db.organization_billing.insert_one(billing_doc(org_id, **PAID))
    await db.users.insert_one(dict(users["admin"]))
    await db.users.insert_one(dict(users["user"]))
    await db.invitations.insert_one(make_invitation(org_id, users["admin"]["id"], token="tok-full",
                                                    email="late@example.com"))
    resp = await http.post(f"{API_PREFIX}/team/accept-invite",
                           json={"token": "tok-full", "password": "Str0ng!Passw0rd", "firstName": "L", "lastName": "A"})
    assert resp.status_code == 403
    assert await db.users.count_documents({"email": "late@example.com"}) == 0
    assert (await db.invitations.find_one({"token": "tok-full"}))["status"] == "pending"


async def test_accept_invite_allowed_under_cap(http, db, org_id, users, billing_env):
    billing_env.setenv("BILLING_TEAM_MAX_MEMBERS", "2")
    await db.organization_billing.insert_one(billing_doc(org_id, **PAID))
    await db.users.insert_one(dict(users["admin"]))
    await db.invitations.insert_one(make_invitation(org_id, users["admin"]["id"], token="tok-ok",
                                                    email="ok@example.com"))
    resp = await http.post(f"{API_PREFIX}/team/accept-invite",
                           json={"token": "tok-ok", "password": "Str0ng!Passw0rd", "firstName": "O", "lastName": "K"})
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------
# P1: viewers cannot upload
# --------------------------------------------------------------------------
@pytest.fixture
def upload_stubs(monkeypatch):
    async def fake_extract(content, mime):
        return "This agreement is between A and B."

    async def fake_s3(content, filename, org_id, mime):
        return f"{org_id}/{filename}"

    async def fake_analyze(text):
        return {"riskLevel": "low", "dates": {}}

    monkeypatch.setattr(contracts_route, "extract_text_from_file", fake_extract)
    monkeypatch.setattr(contracts_route, "upload_file_to_s3", fake_s3)
    monkeypatch.setattr(contracts_route, "analyze_contract", fake_analyze)


@pytest.mark.parametrize("role,expected", [("viewer", 403), ("user", 200), ("manager", 200), ("admin", 200)])
async def test_upload_requires_write_role(http, db, users, upload_stubs, role, expected):
    resp = await http.post(
        f"{API_PREFIX}/contracts",
        data={"title": "NDA"},
        files={"file": ("nda.txt", b"hello", "text/plain")},
        headers=auth_headers(users[role]),
    )
    assert resp.status_code == expected, resp.text
    assert await db.contracts.count_documents({}) == (1 if expected == 200 else 0)
    billing = await db.organization_billing.find_one({})
    # A rejected viewer must not consume trial quota either.
    assert (billing or {}).get("trialContractsUsed", 0) == (1 if expected == 200 else 0)


@pytest.mark.parametrize("role,expected", [("viewer", 403), ("user", 200)])
async def test_bulk_upload_requires_write_role(http, db, users, upload_stubs, role, expected):
    resp = await http.post(
        f"{API_PREFIX}/contracts/bulk",
        files=[("files", ("a.txt", b"hello", "text/plain"))],
        headers=auth_headers(users[role]),
    )
    assert resp.status_code == expected, resp.text
    assert await db.contracts.count_documents({}) == (1 if expected == 200 else 0)
