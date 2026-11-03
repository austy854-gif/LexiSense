"""Ported #78 pieces on top of Option B: entitlement states (grace -> read-only,
unverified plan), BILLING_UI_ENABLED + GET /billing/config, chat/invite gates,
duplicate-subscription guard, trial carry-over, webhook guards, tax labels."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from models.billing import OrganizationBilling
from routes import billing as billing_route
from services import stripe_prices
from utils import billing_access as ba
from utils import plans
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract, make_user
from tests.unit.test_pricing_integrity import (  # noqa: F401  (fixtures)
    FAKE_AMOUNTS, _checkout, fake_stripe, pid, price_env,
)

@pytest.fixture
async def bclient(app):
    """Test app plus the billing router (conftest mounts contracts and team only)."""
    from fastapi import APIRouter
    from httpx import ASGITransport, AsyncClient
    api = APIRouter(prefix=API_PREFIX)
    api.include_router(billing_route.router)
    app.include_router(api)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)


def iso(days, base=NOW):
    return (base + timedelta(days=days)).isoformat()


def paid(plan="team", **kw):
    return {"subscriptionTier": "team", "billingStatus": "active",
            "stripePriceId": pid(plan, "monthly"), **kw}


# --- entitlement states -------------------------------------------------------
class TestEntitlement:
    @pytest.mark.parametrize("plan,cap", [("starter", 5), ("team", 15), ("business", 30)])
    def test_paid_and_trialing_are_paid(self, plan, cap):
        for status in ("active", "trialing"):
            e = ba.compute_entitlement(paid(plan, billingStatus=status), NOW)
            assert (e.state, e.plan_id, e.can_use_ai, e.max_members) == ("paid", plan, True, cap)

    def test_grace_then_read_only(self):
        grace = ba.compute_entitlement(paid(billingStatus="past_due", pastDueSince=iso(-2)), NOW)
        assert grace.state == "grace" and grace.can_use_ai and not grace.read_only
        over = ba.compute_entitlement(paid(billingStatus="past_due", pastDueSince=iso(-9)), NOW)
        assert over.state == "past_due" and over.read_only and not over.can_use_ai
        assert over.max_members == 15  # keeps its plan's seats, just can't add more

    def test_unknown_price_is_unverified(self):
        e = ba.compute_entitlement(paid(stripePriceId="price_unknown"), NOW)
        assert e.state == "unverified_plan" and e.blocked and e.max_members == 3

    def test_legacy_price_keeps_access(self, monkeypatch):
        monkeypatch.setenv("STRIPE_LEGACY_PRICE_IDS", "price_old_team")
        e = ba.compute_entitlement(paid(stripePriceId="price_old_team"), NOW)
        assert e.state == "paid" and e.can_use_ai

    def test_trial_and_expired_trial(self):
        live = {"subscriptionTier": "trial", "billingStatus": "trialing", "trialEndDate": iso(3)}
        assert ba.compute_entitlement(live, NOW).state == "trial"
        dead = {**live, "trialEndDate": iso(-1)}
        e = ba.compute_entitlement(dead, NOW)
        assert e.state == "trial_expired" and e.blocked

    def test_cancelled(self):
        e = ba.compute_entitlement({"subscriptionTier": "cancelled", "billingStatus": "cancelled"}, NOW)
        assert e.state == "cancelled" and e.blocked

    def test_cancelled_tier_takes_precedence_over_past_due(self):
        e = ba.compute_entitlement({"subscriptionTier": "cancelled", "billingStatus": "past_due"}, NOW)
        assert e.state == "cancelled" and e.blocked


# --- BILLING_UI_ENABLED -------------------------------------------------------
class TestBillingUiFlag:
    async def test_config_endpoint_default_off(self, bclient, monkeypatch):
        monkeypatch.delenv("BILLING_UI_ENABLED", raising=False)
        r = await bclient.get(f"{API_PREFIX}/billing/config")
        assert r.status_code == 200 and r.json() == {"billingUiEnabled": False}

    async def test_config_endpoint_on(self, bclient, monkeypatch):
        monkeypatch.setenv("BILLING_UI_ENABLED", "true")
        r = await bclient.get(f"{API_PREFIX}/billing/config")
        assert r.json() == {"billingUiEnabled": True}

    @pytest.mark.parametrize("state", ["trial_expired", "past_due", "cancelled"])
    def test_messages_follow_flag(self, monkeypatch, state):
        monkeypatch.setenv("BILLING_UI_ENABLED", "false")
        assert "contact support" in ba.blocked_message(state)
        monkeypatch.setenv("BILLING_UI_ENABLED", "true")
        assert "contact support" not in ba.blocked_message(state)


# --- request gates ------------------------------------------------------------
async def _seed_billing(db, org_id, **state):
    await db.organization_billing.insert_one({"id": f"b-{org_id}", "organizationId": org_id, **state})


async def _seed_user(db, org_id, role="admin"):
    user = make_user(org_id, role=role)
    await db.users.insert_one(dict(user))
    return user


class TestGates:
    async def test_chat_blocked_after_grace(self, client, db, org_id):
        await _seed_billing(db, org_id, **paid(billingStatus="past_due", pastDueSince=iso(-9, datetime.now(timezone.utc))))
        user = await _seed_user(db, org_id)
        contract = make_contract(org_id, user["id"])
        await db.contracts.insert_one(dict(contract))
        r = await client.post(f"{API_PREFIX}/contracts/{contract['id']}/chat",
                              json={"question": "hi"}, headers=auth_headers(user))
        assert r.status_code == 403 and "read-only" in r.json()["detail"]

    async def test_chat_blocked_after_trial(self, client, db, org_id):
        await _seed_billing(db, org_id, subscriptionTier="trial", billingStatus="trialing",
                            trialStartDate=iso(-20, datetime.now(timezone.utc)),
                            trialEndDate=iso(-1, datetime.now(timezone.utc)))
        user = await _seed_user(db, org_id)
        contract = make_contract(org_id, user["id"])
        await db.contracts.insert_one(dict(contract))
        r = await client.post(f"{API_PREFIX}/contracts/{contract['id']}/chat",
                              json={"question": "hi"}, headers=auth_headers(user))
        assert r.status_code == 403 and "trial has ended" in r.json()["detail"]

    async def test_invite_blocked_when_read_only(self, client, db, org_id):
        await _seed_billing(db, org_id, **paid(billingStatus="past_due", pastDueSince=iso(-9, datetime.now(timezone.utc))))
        admin = await _seed_user(db, org_id)
        r = await client.post(f"{API_PREFIX}/team/invite", json={"email": "n@x.com"}, headers=auth_headers(admin))
        assert r.status_code == 403 and "read-only" in r.json()["detail"]

    async def test_invite_allowed_in_grace(self, client, db, org_id):
        await _seed_billing(db, org_id, **paid(billingStatus="past_due", pastDueSince=iso(-1, datetime.now(timezone.utc))))
        admin = await _seed_user(db, org_id)
        r = await client.post(f"{API_PREFIX}/team/invite", json={"email": "n@x.com"}, headers=auth_headers(admin))
        assert r.status_code == 200, r.text

    async def test_subscription_reports_access_state(self, bclient, db, org_id, monkeypatch):
        monkeypatch.setenv("BILLING_UI_ENABLED", "true")
        await _seed_billing(db, org_id, **paid("business"))
        admin = await _seed_user(db, org_id)
        body = (await bclient.get(f"{API_PREFIX}/billing/subscription", headers=auth_headers(admin))).json()
        assert body["accessState"] == "paid" and body["planId"] == "business"
        assert body["maxMembers"] == 30 and body["billingUiEnabled"] is True


# --- checkout: duplicates and trial carry-over --------------------------------
class TestCheckout:
    async def test_second_checkout_409_from_db(self, app, db, fake_stripe, org_id):
        await _seed_billing(db, org_id, **paid())
        with pytest.raises(HTTPException) as exc:
            await _checkout(org_id, planId="team")
        assert exc.value.status_code == 409 and fake_stripe.sessions == []

    async def test_second_checkout_409_from_stripe(self, app, db, fake_stripe, org_id, monkeypatch):
        await _seed_billing(db, org_id, subscriptionTier="trial", billingStatus="trialing",
                            stripeCustomerId="cus_9", trialEndDate=iso(3, datetime.now(timezone.utc)))
        monkeypatch.setattr(billing_route.stripe.Subscription, "list",
                            lambda **kw: {"data": [{"status": "active"}]})
        with pytest.raises(HTTPException) as exc:
            await _checkout(org_id, planId="team")
        assert exc.value.status_code == 409 and fake_stripe.sessions == []

    async def test_stripe_error_is_503(self, app, db, fake_stripe, org_id, monkeypatch):
        await _seed_billing(db, org_id, subscriptionTier="trial", billingStatus="trialing", stripeCustomerId="cus_9")

        def boom(**kw):
            raise billing_route.stripe.StripeError("down")
        monkeypatch.setattr(billing_route.stripe.Subscription, "list", boom)
        with pytest.raises(HTTPException) as exc:
            await _checkout(org_id, planId="team")
        assert exc.value.status_code == 503

    def test_trial_remainder_carried_as_trial_end(self):
        b = OrganizationBilling(organizationId="o", trialEndDate=iso(5))
        data = billing_route.checkout_subscription_data(b, "o", now=NOW)
        assert data["trial_end"] == int((NOW + timedelta(days=5)).timestamp())
        assert "trial_period_days" not in data

    def test_short_trial_remainder_not_carried(self):
        b = OrganizationBilling(organizationId="o", trialEndDate=(NOW + timedelta(hours=47)).isoformat())
        data = billing_route.checkout_subscription_data(b, "o", now=NOW)
        assert "trial_end" not in data and "trial_period_days" not in data


# --- webhooks ------------------------------------------------------------------
def _sub(sub_id, status="active", price=None, **kw):
    return {"id": sub_id, "status": status, "customer": "cus_1", "metadata": {},
            "items": {"data": [{"price": {"id": price or pid("team", "monthly")}}]}, **kw}


class TestWebhooks:
    @pytest.fixture(autouse=True)
    def _db(self, db):
        billing_route.init_db(db)

    async def test_deleting_duplicate_keeps_org_paid(self, db, org_id):
        await _seed_billing(db, org_id, **paid(stripeSubscriptionId="sub_live", stripeCustomerId="cus_1"))
        await billing_route.handle_subscription_deleted(_sub("sub_dup", "canceled"))
        doc = await db.organization_billing.find_one({"organizationId": org_id})
        assert doc["billingStatus"] == "active" and doc["stripeSubscriptionId"] == "sub_live"

    async def test_dead_duplicate_update_ignored(self, db, org_id):
        await _seed_billing(db, org_id, **paid(stripeSubscriptionId="sub_live", stripeCustomerId="cus_1"))
        await billing_route.update_billing_from_subscription(org_id, _sub("sub_dup", "canceled"), "cus_1")
        doc = await db.organization_billing.find_one({"organizationId": org_id})
        assert doc["billingStatus"] == "active" and doc["stripeSubscriptionId"] == "sub_live"

    async def test_trial_end_preserved_and_past_due_since_kept(self, db, org_id):
        await _seed_billing(db, org_id, subscriptionTier="trial", billingStatus="trialing",
                            trialEndDate="2026-11-03T00:00:00+00:00", stripeCustomerId="cus_1")
        await billing_route.update_billing_from_subscription(org_id, _sub("sub_1", "past_due"), "cus_1")
        first = await db.organization_billing.find_one({"organizationId": org_id})
        assert first["trialEndDate"] == "2026-11-03T00:00:00+00:00"
        await billing_route.update_billing_from_subscription(org_id, _sub("sub_1", "past_due"), "cus_1")
        second = await db.organization_billing.find_one({"organizationId": org_id})
        assert second["pastDueSince"] == first["pastDueSince"]

    async def test_unknown_price_logged_and_unverified(self, db, org_id, caplog):
        await _seed_billing(db, org_id, subscriptionTier="trial", billingStatus="trialing", stripeCustomerId="cus_1")
        with caplog.at_level("ERROR"):
            await billing_route.update_billing_from_subscription(org_id, _sub("sub_1", price="price_x"), "cus_1")
        assert "unconfigured Stripe price" in caplog.text
        doc = await db.organization_billing.find_one({"organizationId": org_id})
        assert ba.compute_entitlement(doc).state == "unverified_plan"


# --- tax (B: tax_behavior=inclusive per STRIPE_SETUP.md) ------------------------
class TestTax:
    def test_label(self):
        assert plans.price_label("usd", "inclusive") == "USD, tax included"
        assert plans.price_label("usd", "exclusive") == "USD, plus applicable tax"

    def test_default_and_invalid(self, monkeypatch, caplog):
        monkeypatch.delenv("BILLING_TAX_BEHAVIOR", raising=False)
        assert plans.tax_behavior() == "inclusive"
        monkeypatch.setenv("BILLING_TAX_BEHAVIOR", "bogus")
        with caplog.at_level("ERROR"):
            assert plans.tax_behavior() == "inclusive"
        assert "invalid" in caplog.text

    async def test_plans_tax_inclusive_from_config(self, bclient, monkeypatch):
        monkeypatch.setattr(stripe_prices, "_fetch_price", lambda pid_: None)
        body = (await bclient.get(f"{API_PREFIX}/billing/plans")).json()
        paid_cards = [p for p in body["plans"] if p["id"] != "trial"]
        assert paid_cards and all(p["taxInclusive"] and p["priceLabel"] == "USD, tax included" for p in paid_cards)
        assert all(p["annual"]["taxInclusive"] for p in paid_cards)

    async def test_stripe_tax_behavior_wins_and_mismatch_logged(self, bclient, fake_stripe, monkeypatch, caplog):
        orig = stripe_prices.stripe.Price.retrieve
        monkeypatch.setattr(stripe_prices.stripe.Price, "retrieve",
                            lambda price_id: {**orig(price_id), "tax_behavior": "exclusive"})
        with caplog.at_level("ERROR"):
            body = (await bclient.get(f"{API_PREFIX}/billing/plans")).json()
        team = next(p for p in body["plans"] if p["id"] == "team")
        assert team["taxBehavior"] == "exclusive" and team["taxInclusive"] is False
        assert team["priceLabel"] == "USD, plus applicable tax"
        assert "Tax behaviour mismatch" in caplog.text
