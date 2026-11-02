"""Displayed price == charged price.

``/billing/plans`` must show what checkout will charge. Both read the Stripe
price ID from ``utils.plans`` and the amount is taken from that Stripe Price
object (cached), so the two cannot drift. If Stripe is unavailable the optional
``BILLING_<PLAN>_PRICE_<INTERVAL>`` env value is shown instead, and a mismatch
between that env value and Stripe is logged as an error.
"""
import logging
import time
from typing import Dict, Optional, Tuple

import stripe

from utils.plans import CHECKOUT_INTERVALS, PlanConfig, price_label, tax_behavior

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600
_cache: Dict[str, Tuple[float, dict]] = {}


def clear_cache() -> None:
    _cache.clear()


def _fetch_price(price_id: str) -> Optional[dict]:
    now = time.monotonic()
    hit = _cache.get(price_id)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    if not stripe.api_key:
        return None
    try:
        price = stripe.Price.retrieve(price_id)
    except stripe.StripeError as e:
        logger.warning("Could not load Stripe price %s: %s", price_id, type(e).__name__)
        return hit[1] if hit else None
    data = {
        "unit_amount": price.get("unit_amount"),
        "currency": price.get("currency"),
        "interval": (price.get("recurring") or {}).get("interval"),
        "tax_behavior": price.get("tax_behavior"),
    }
    _cache[price_id] = (now, data)
    return data


def display_price(plan: PlanConfig, interval: str) -> dict:
    """``{"priceId", "amount", "currency", "source", "taxBehavior", "taxInclusive", "priceLabel"}`` for plan + interval.

    ``source`` is ``"stripe"`` (amount of the exact price checkout charges),
    ``"config"`` (env fallback, Stripe unreachable) or ``None`` (unknown).
    """
    price_id = plan.price_for_interval(interval)
    fallback = plan.fallback_prices.get(interval)
    configured_tax = tax_behavior()
    result = {"priceId": price_id, "amount": None, "currency": "usd", "source": None,
              "taxBehavior": configured_tax}
    if not price_id:
        return _with_tax_label(result)
    stripe_price = _fetch_price(price_id)
    if stripe_price and stripe_price.get("unit_amount") is not None:
        amount = stripe_price["unit_amount"] / 100
        amount = int(amount) if float(amount).is_integer() else amount
        expected_interval = CHECKOUT_INTERVALS[interval]
        if stripe_price.get("interval") and stripe_price["interval"] != expected_interval:
            logger.error("Stripe price %s (%s %s) recurs every %s, expected %s",
                         price_id, plan.id, interval, stripe_price["interval"], expected_interval)
        if fallback is not None and fallback != amount:
            logger.error("Display price mismatch for %s %s: config %s, Stripe %s (showing Stripe)",
                         plan.id, interval, fallback, amount)
        stripe_tax = stripe_price.get("tax_behavior")
        if stripe_tax and stripe_tax != configured_tax:
            logger.error("Tax behaviour mismatch for %s %s: config %s, Stripe %s (showing Stripe)",
                         plan.id, interval, configured_tax, stripe_tax)
        result.update(amount=amount, currency=stripe_price.get("currency") or "usd", source="stripe",
                      taxBehavior=stripe_tax or configured_tax)
    elif fallback is not None:
        result.update(amount=fallback, source="config")
    return _with_tax_label(result)


def _with_tax_label(result: dict) -> dict:
    result["taxInclusive"] = result["taxBehavior"] == "inclusive"
    result["priceLabel"] = price_label(result["currency"], result["taxBehavior"])
    return result
