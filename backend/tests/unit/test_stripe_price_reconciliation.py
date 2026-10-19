"""B9: the displayed price is checked against the Stripe Price objects.

stripe.Price.retrieve is always faked here (no network).
"""
import logging

import pytest
import stripe

from services import stripe_prices
from utils import plans as plans_module
from tests.unit.conftest import API_PREFIX
from tests.unit.test_billing_integrity import (  # noqa: F401
    PRICE_ANNUAL,
    PRICE_MONTHLY,
    billing_env,
    full_app,
    http,
)


def _price(amount, currency="usd", interval="month", count=1):
    return stripe.StripeObject.construct_from(
        {"id": "p", "unit_amount": amount, "currency": currency,
         "recurring": {"interval": interval, "interval_count": count}},
        "sk_test_fake",
    )


@pytest.fixture
def stripe_prices_fake(monkeypatch):
    """Configurable fake for stripe.Price.retrieve that records calls."""
    state = {"prices": {}, "calls": [], "error": None}

    def retrieve(price_id, **kwargs):
        state["calls"].append(price_id)
        if state["error"] is not None:
            raise state["error"]
        return state["prices"][price_id]

    monkeypatch.setattr(stripe, "api_key", "sk_test_fake")
    monkeypatch.setattr(stripe.Price, "retrieve", staticmethod(retrieve))
    return state


def team():
    return plans_module.get_paid_plans()["team"]


def test_matching_price_is_verified_without_error(stripe_prices_fake, caplog):
    stripe_prices_fake["prices"][PRICE_MONTHLY] = _price(4900)
    with caplog.at_level(logging.ERROR):
        shown = stripe_prices.reconcile_display_price(team(), "monthly")
    assert (shown.amount, shown.currency, shown.interval, shown.source, shown.mismatch) == (49, "usd", "month", "stripe", False)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("stripe_price,expected,problem", [
    (_price(5900), (59, "usd", "month"), "amount 49 != Stripe 59"),
    (_price(4999), (49.99, "usd", "month"), "amount 49 != Stripe 49.99"),
    (_price(4900, currency="cad"), (49, "cad", "month"), "currency usd != Stripe cad"),
    (_price(4900, interval="year"), (49, "usd", "year"), "interval month != Stripe 1 x year"),
    (_price(4900, count=3), (49, "usd", "month"), "interval month != Stripe 3 x month"),
])
def test_mismatch_logs_error_and_serves_stripe_amount(stripe_prices_fake, caplog, stripe_price, expected, problem):
    stripe_prices_fake["prices"][PRICE_MONTHLY] = stripe_price
    with caplog.at_level(logging.ERROR):
        shown = stripe_prices.reconcile_display_price(team(), "monthly")
    assert (shown.amount, shown.currency, shown.interval) == expected
    assert shown.mismatch and shown.source == "stripe"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and problem in errors[0] and PRICE_MONTHLY in errors[0]


def test_zero_decimal_currency(stripe_prices_fake, billing_env, caplog):
    billing_env.setenv("BILLING_CURRENCY", "JPY")
    billing_env.setenv("BILLING_TEAM_PRICE_MONTHLY", "7000")
    stripe_prices_fake["prices"][PRICE_MONTHLY] = _price(7000, currency="jpy")
    with caplog.at_level(logging.ERROR):
        shown = stripe_prices.reconcile_display_price(team(), "monthly")
    assert (shown.amount, shown.currency, shown.mismatch) == (7000, "jpy", False)
    assert not caplog.records or all(r.levelno < logging.ERROR for r in caplog.records)


def test_annual_without_display_price_uses_stripe_without_error(stripe_prices_fake, caplog):
    stripe_prices_fake["prices"][PRICE_ANNUAL] = _price(49000, interval="year")
    with caplog.at_level(logging.ERROR):
        shown = stripe_prices.reconcile_display_price(team(), "annual")
    assert (shown.amount, shown.interval, shown.mismatch) == (490, "year", False)
    assert all(r.levelno < logging.ERROR for r in caplog.records)


def test_annual_display_price_is_checked(stripe_prices_fake, billing_env):
    billing_env.setenv("BILLING_TEAM_PRICE_ANNUAL", "490")
    stripe_prices_fake["prices"][PRICE_ANNUAL] = _price(52900, interval="year")
    shown = stripe_prices.reconcile_display_price(team(), "annual")
    assert shown.amount == 529 and shown.mismatch


def test_lookups_are_cached(stripe_prices_fake):
    stripe_prices_fake["prices"][PRICE_MONTHLY] = _price(4900)
    for _ in range(3):
        stripe_prices.reconcile_display_price(team(), "monthly")
    assert stripe_prices_fake["calls"] == [PRICE_MONTHLY]


def test_cache_expires(stripe_prices_fake):
    stripe_prices_fake["prices"][PRICE_MONTHLY] = _price(4900)
    stripe_prices.retrieve_price(PRICE_MONTHLY, now=0.0)
    stripe_prices.retrieve_price(PRICE_MONTHLY, now=stripe_prices.PRICE_CACHE_TTL_SECONDS - 1)
    assert len(stripe_prices_fake["calls"]) == 1
    stripe_prices.retrieve_price(PRICE_MONTHLY, now=stripe_prices.PRICE_CACHE_TTL_SECONDS + 1)
    assert len(stripe_prices_fake["calls"]) == 2


def test_stripe_error_falls_back_to_config_and_is_cached_briefly(stripe_prices_fake):
    stripe_prices_fake["error"] = stripe.APIConnectionError("down")
    shown = stripe_prices.reconcile_display_price(team(), "monthly")
    assert (shown.amount, shown.source) == (49, "config")
    stripe_prices.reconcile_display_price(team(), "monthly")
    assert len(stripe_prices_fake["calls"]) == 1  # negative result cached
    stripe_prices_fake["error"] = None
    stripe_prices_fake["prices"][PRICE_MONTHLY] = _price(4900)
    stripe_prices.retrieve_price(PRICE_MONTHLY, now=10**9)  # after the error TTL
    assert len(stripe_prices_fake["calls"]) == 2


def test_no_stripe_key_uses_config_without_calling_stripe(stripe_prices_fake, monkeypatch):
    monkeypatch.setattr(stripe, "api_key", None)
    shown = stripe_prices.reconcile_display_price(team(), "monthly")
    assert (shown.amount, shown.source) == (49, "config")
    assert stripe_prices_fake["calls"] == []


def test_price_without_unit_amount_falls_back_with_error(stripe_prices_fake, caplog):
    stripe_prices_fake["prices"][PRICE_MONTHLY] = _price(None)
    with caplog.at_level(logging.ERROR):
        shown = stripe_prices.reconcile_display_price(team(), "monthly")
    assert shown.source == "config" and shown.amount == 49
    assert any("no fixed unit_amount" in r.getMessage() for r in caplog.records)


def test_no_price_id_configured(stripe_prices_fake, billing_env):
    billing_env.delenv("STRIPE_TEAM_PRICE_ID")
    billing_env.delenv("STRIPE_TEAM_PRICE_ID_ANNUAL")
    assert stripe_prices.reconcile_display_price(team(), "monthly").source == "config"
    assert stripe_prices.reconcile_display_price(team(), "annual") is None
    assert stripe_prices_fake["calls"] == []


async def test_plans_endpoint_serves_stripe_amount_on_mismatch(http, stripe_prices_fake, caplog):
    stripe_prices_fake["prices"][PRICE_MONTHLY] = _price(5900)
    stripe_prices_fake["prices"][PRICE_ANNUAL] = _price(59000, interval="year")
    with caplog.at_level(logging.ERROR):
        body = (await http.get(f"{API_PREFIX}/billing/plans")).json()
    team_plan = body["plans"][1]
    assert (team_plan["price"], team_plan["currency"], team_plan["interval"]) == (59, "usd", "month")
    assert team_plan["prices"] == {
        "monthly": {"amount": 59, "currency": "usd", "interval": "month", "verified": True},
        "annual": {"amount": 590, "currency": "usd", "interval": "year", "verified": True},
    }
    assert team_plan["intervals"] == ["monthly", "annual"]
    assert "price_" not in str(body)  # still no Stripe IDs in the response
    assert any("does not match Stripe price" in r.getMessage() for r in caplog.records)


async def test_plans_endpoint_hides_annual_when_no_price_can_be_shown(http, stripe_prices_fake):
    """An annual Stripe price that can't be looked up and has no display price
    isn't offered: we never sell an interval without showing its price."""
    stripe_prices_fake["error"] = stripe.APIConnectionError("down")
    team_plan = (await http.get(f"{API_PREFIX}/billing/plans")).json()["plans"][1]
    assert team_plan["intervals"] == ["monthly"]
    assert team_plan["prices"]["monthly"]["verified"] is False
