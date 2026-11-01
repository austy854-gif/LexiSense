"""Regression tests for pricing integrity (Option B tiers).

1. Checkout only accepts the six STRIPE_PRICE_* ids; empty allowlist fails closed.
2. Checkout omits ``trial_period_days`` once the org has started its in-app trial.
3. /billing/plans takes prices from the same Stripe price checkout charges.
4. Team invites enforce the tier seat cap (active users + pending invites).
5. Parity: /plans price + priceId == what checkout charges, per tier and interval.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from models.billing import CheckoutSessionRequest, OrganizationBilling
from models.invitation import Invitation
from routes import billing as billing_route
from routes import team as team_route
from services import stripe_prices
from utils import plans
from tests.unit.conftest import API_PREFIX, auth_headers, make_user

INVITE_URL = f"{API_PREFIX}/team/invite"
TIERS = ("starter", "team", "business")
INTERVALS = ("monthly", "annual")
# Fake Stripe catalogue (cents). Test data only, never read by app code.
FAKE_AMOUNTS = {
    "starter": {"monthly": 1100, "annual": 11000},
    "team": {"monthly": 2200, "annual": 22000},
    "business": {"monthly": 3300, "annual": 33000},
}


def pid(plan, interval):
    return f"price_{plan}_{interval}"


@pytest.fixture(autouse=True)
def price_env(monkeypatch):
    for name in list(plans.STRIPE_PRICE_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    for plan in TIERS:
        for interval in INTERVALS:
            monkeypatch.setenv(plans.stripe_price_env_var(plan, interval), pid(plan, interval))
        for suffix in ("MAX_MEMBERS", "PRICE_MONTHLY", "PRICE_ANNUAL"):
            monkeypatch.delenv(f"BILLING_{plan.upper()}_{suffix}", raising=False)
    monkeypatch.delenv("BILLING_TRIAL_MAX_MEMBERS", raising=False)
    stripe_prices.clear_cache()
    yield
    stripe_prices.clear_cache()


@pytest.fixture
def fake_stripe(monkeypatch):
    """Stub Stripe: Price.retrieve from FAKE_AMOUNTS, record Checkout sessions."""
    sessions = []
    lookups = []
    by_id = {pid(p, i): (a, "month" if i == "monthly" else "year")
             for p, d in FAKE_AMOUNTS.items() for i, a in d.items()}

    def retrieve(price_id):
        lookups.append(price_id)
        amount, rec = by_id[price_id]
        return {"id": price_id, "unit_amount": amount, "currency": "usd", "recurring": {"interval": rec}}

    def create_session(**kw):
        sessions.append(kw)
        return SimpleNamespace(id="cs_1", url="https://stripe.test/cs_1")

    monkeypatch.setattr(stripe_prices.stripe, "api_key", "sk_test_x")
    monkeypatch.setattr(stripe_prices.stripe.Price, "retrieve", retrieve)
    monkeypatch.setattr(billing_route, "stripe_api_key", "sk_test_x")
    monkeypatch.setattr(billing_route.stripe.Customer, "create", lambda **kw: SimpleNamespace(id="cus_1"))
    monkeypatch.setattr(billing_route.stripe.checkout.Session, "create", create_session)
    return SimpleNamespace(sessions=sessions, lookups=lookups, by_id=by_id)


def _req(**kw):
    return CheckoutSessionRequest(successUrl="https://a/s", cancelUrl="https://a/c", **kw)


async def _checkout(org_id, **kw):
    admin = make_user(org_id, role="admin")
    return await billing_route.create_checkout_session(
        _req(**kw), current_user={"organizationId": org_id, "email": admin["email"], "sub": admin["id"]},
    )


# --- 1. allowlist -------------------------------------------------------------
class TestPriceAllowlist:
    def test_allowlist_is_exactly_the_six_prices(self):
        assert plans.allowed_price_ids() == {pid(p, i) for p in TIERS for i in INTERVALS}

    def test_retired_single_price_var_is_not_allowed(self, monkeypatch):
        monkeypatch.setenv("STRIPE_TEAM_PRICE_ID", "price_old")
        with pytest.raises(HTTPException) as exc:
            billing_route.resolve_checkout_price(_req(priceId="price_old"))
        assert exc.value.status_code == 400

    @pytest.mark.parametrize("plan", TIERS)
    @pytest.mark.parametrize("interval", INTERVALS)
    def test_plan_and_interval_resolve(self, plan, interval):
        assert billing_route.resolve_checkout_price(_req(planId=plan, interval=interval)) == pid(plan, interval)

    def test_legacy_price_id_on_allowlist_accepted(self):
        assert billing_route.resolve_checkout_price(_req(priceId=" price_team_annual ")) == "price_team_annual"

    @pytest.mark.parametrize("kw", [{"priceId": "price_attacker_1cent"}, {"planId": "enterprise"},
                                    {"planId": "team", "interval": "weekly"}, {}])
    def test_anything_else_rejected_400(self, kw):
        with pytest.raises(HTTPException) as exc:
            billing_route.resolve_checkout_price(_req(**kw))
        assert exc.value.status_code == 400

    def test_empty_allowlist_fails_closed_and_logs(self, monkeypatch, caplog):
        for name in plans.STRIPE_PRICE_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        with caplog.at_level("ERROR"), pytest.raises(HTTPException) as exc:
            billing_route.resolve_checkout_price(_req(planId="team"))
        assert exc.value.status_code == 400
        assert "allowlist is empty" in caplog.text

    async def test_rejected_before_calling_stripe(self, app, fake_stripe, org_id):
        with pytest.raises(HTTPException):
            await _checkout(org_id, priceId="price_evil")
        assert fake_stripe.sessions == []


# --- 2. no second trial -------------------------------------------------------
class TestTrial:
    async def test_checkout_omits_trial_after_in_app_trial(self, app, fake_stripe, org_id):
        await _checkout(org_id, planId="team")
        (call,) = fake_stripe.sessions
        assert "trial_period_days" not in call["subscription_data"]

    def test_decision(self):
        used = OrganizationBilling(organizationId="o")
        assert "trial_period_days" not in billing_route.checkout_subscription_data(used, "o")
        fresh = OrganizationBilling(organizationId="o", trialStartDate="")
        assert billing_route.checkout_subscription_data(fresh, "o")["trial_period_days"] == billing_route.TRIAL_DAYS


# --- 3 + 5. /plans prices and parity with checkout ----------------------------
class TestPlansAndParity:
    async def test_plans_list_tiers_with_seat_caps(self, fake_stripe):
        cards = {c["id"]: c for c in (await billing_route.get_plans())["plans"]}
        assert [k for k in cards] == ["trial", *TIERS]
        assert {k: cards[k]["maxMembers"] for k in cards} == {"trial": 3, "starter": 5, "team": 15, "business": 30}

    @pytest.mark.parametrize("plan", TIERS)
    @pytest.mark.parametrize("interval", INTERVALS)
    async def test_shown_price_matches_charged_price(self, app, fake_stripe, org_id, plan, interval):
        card = next(c for c in (await billing_route.get_plans())["plans"] if c["id"] == plan)
        shown = card if interval == "monthly" else card["annual"]
        await _checkout(org_id, planId=plan, interval=interval)
        charged_price_id = fake_stripe.sessions[-1]["line_items"][0]["price"]
        assert shown["priceId"] == charged_price_id
        assert shown["priceSource"] == "stripe"
        assert shown["price"] * 100 == fake_stripe.by_id[charged_price_id][0]

    async def test_config_mismatch_is_logged_and_stripe_wins(self, fake_stripe, monkeypatch, caplog):
        monkeypatch.setenv("BILLING_TEAM_PRICE_MONTHLY", "1")
        with caplog.at_level("ERROR"):
            card = next(c for c in (await billing_route.get_plans())["plans"] if c["id"] == "team")
        assert card["price"] == FAKE_AMOUNTS["team"]["monthly"] / 100
        assert "Display price mismatch" in caplog.text

    async def test_stripe_down_falls_back_to_config(self, monkeypatch):
        monkeypatch.setattr(stripe_prices.stripe, "api_key", None)
        monkeypatch.setenv("BILLING_STARTER_PRICE_MONTHLY", "7")
        card = next(c for c in (await billing_route.get_plans())["plans"] if c["id"] == "starter")
        assert (card["price"], card["priceSource"]) == (7, "config")
        team = next(c for c in (await billing_route.get_plans())["plans"] if c["id"] == "team")
        assert (team["price"], team["priceSource"]) == (None, None)

    async def test_price_lookups_are_cached(self, fake_stripe):
        await billing_route.get_plans()
        await billing_route.get_plans()
        assert len(fake_stripe.lookups) == 6

    def test_no_hardcoded_prices_in_app_code(self):
        import inspect
        import re
        for mod in (plans, stripe_prices, billing_route):
            src = inspect.getsource(mod)
            assert not re.search(r"\b(49|79|99|129|249)\b", src), mod.__name__


# --- 4. seat cap per tier -----------------------------------------------------
def _invite(org_id, expires_in=timedelta(days=7), status="pending"):
    return Invitation(
        organizationId=org_id, email=f"{uuid.uuid4().hex[:8]}@x.com", invitedBy="a",
        tokenHash=uuid.uuid4().hex, status=status,
        expiresAt=(datetime.now(timezone.utc) + expires_in).isoformat(),
    ).model_dump()


async def _seed(db, org_id, members, invites=0, billing=None):
    admin = make_user(org_id, role="admin", user_id="user-admin")
    await db.users.insert_one(dict(admin))
    for _ in range(members - 1):
        await db.users.insert_one(make_user(org_id))
    for _ in range(invites):
        await db.invitations.insert_one(_invite(org_id))
    if billing:
        await db.organization_billing.insert_one({"organizationId": org_id, **billing})
    return admin


def _paid(plan, status="active"):
    return {"subscriptionTier": "team", "billingStatus": status, "stripePriceId": pid(plan, "monthly")}


class TestSeatCap:
    @pytest.mark.parametrize("billing,cap", [
        (None, 3),
        (_paid("starter"), 5),
        (_paid("team"), 15),
        (_paid("business"), 30),
        ({"subscriptionTier": "team", "billingStatus": "active", "stripePriceId": "price_unknown"}, 3),
        ({**_paid("business"), "subscriptionTier": "cancelled", "billingStatus": "cancelled"}, 3),
    ])
    async def test_cap_per_tier(self, client, db, org_id, billing, cap):
        admin = await _seed(db, org_id, members=cap - 2, invites=1, billing=billing)
        ok = await client.post(INVITE_URL, json={"email": "last@x.com"}, headers=auth_headers(admin))
        assert ok.status_code == 200, ok.text
        r = await client.post(INVITE_URL, json={"email": "over@x.com"}, headers=auth_headers(admin))
        assert r.status_code == 403
        assert f"up to {cap} team members" in r.json()["detail"] and "Upgrade" in r.json()["detail"]
        assert await db.invitations.count_documents({"email": "over@x.com"}) == 0

    async def test_env_override(self, client, db, org_id, monkeypatch):
        monkeypatch.setenv("BILLING_TRIAL_MAX_MEMBERS", "1")
        admin = await _seed(db, org_id, members=1)
        r = await client.post(INVITE_URL, json={"email": "x@x.com"}, headers=auth_headers(admin))
        assert r.status_code == 403

    async def test_deactivated_and_expired_do_not_count(self, client, db, org_id):
        admin = await _seed(db, org_id, members=2)
        gone = make_user(org_id)
        gone["isActive"] = False
        await db.users.insert_one(gone)
        await db.users.insert_one(make_user("other-org"))
        await db.invitations.insert_one(_invite(org_id, expires_in=timedelta(days=-1)))
        await db.invitations.insert_one(_invite(org_id, status="accepted"))
        r = await client.post(INVITE_URL, json={"email": "new@x.com"}, headers=auth_headers(admin))
        assert r.status_code == 200, r.text

    async def test_race_loser_withdraws_its_invite(self, client, db, org_id, monkeypatch):
        """A concurrent invite lands between the pre-check and our insert."""
        admin = await _seed(db, org_id, members=2)
        coll = type(db.invitations)
        real_insert = coll.insert_one
        sent = []

        async def racing_insert(self, doc, *a, **kw):
            if doc.get("email") == "new@x.com":
                await real_insert(self, _invite(org_id))
            return await real_insert(self, doc, *a, **kw)

        async def _send(*a, **kw):
            sent.append(kw)

        monkeypatch.setattr(coll, "insert_one", racing_insert)
        monkeypatch.setattr(team_route, "send_invitation_email", _send)
        r = await client.post(INVITE_URL, json={"email": "new@x.com"}, headers=auth_headers(admin))
        assert r.status_code == 403
        assert await db.invitations.count_documents({"email": "new@x.com"}) == 0
        assert sent == []
