"""Reconcile the prices we display with the Stripe Price objects we charge (B9).

The plan config (``utils.plans``) holds display prices (``BILLING_TEAM_PRICE_*``,
``BILLING_CURRENCY``) and the Stripe price IDs used at checkout. Nothing used to
check that the two agree, so the Billing page could advertise one amount while
Stripe charged another.

``reconcile_display_price`` looks up the configured Stripe price (cached
``stripe.Price.retrieve``) and compares ``unit_amount``, ``currency`` and
``recurring.interval`` with the configured display values. On a mismatch it
logs an ERROR and returns Stripe's values -- what the customer will actually
be charged. When Stripe can't be asked (no API key, network error, a price
without a fixed ``unit_amount``) it falls back to the configured display price.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

import stripe

from utils.plans import CHECKOUT_INTERVALS, PlanConfig

logger = logging.getLogger(__name__)

#: Successful lookups are cached for an hour; failures for a minute so a Stripe
#: outage doesn't turn every /billing/plans request into a Stripe call.
PRICE_CACHE_TTL_SECONDS = 3600.0
PRICE_ERROR_TTL_SECONDS = 60.0

#: Stripe currencies whose ``unit_amount`` is already in whole units.
ZERO_DECIMAL_CURRENCIES = frozenset({
    "bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf",
    "ugx", "vnd", "vuv", "xaf", "xof", "xpf",
})

_cache: Dict[str, Tuple[float, Optional[Any]]] = {}
_lock = threading.Lock()


def clear_price_cache() -> None:
    with _lock:
        _cache.clear()


def _get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    try:
        return obj.get(key, default)
    except AttributeError:
        return getattr(obj, key, default)


def retrieve_price(price_id: str, now: Optional[float] = None) -> Optional[Any]:
    """``stripe.Price.retrieve`` with a small TTL cache. ``None`` when unavailable."""
    if not price_id or not stripe.api_key:
        return None
    now = time.monotonic() if now is None else now
    with _lock:
        hit = _cache.get(price_id)
        if hit is not None and hit[0] > now:
            return hit[1]
    try:
        price = stripe.Price.retrieve(price_id)
        ttl = PRICE_CACHE_TTL_SECONDS
    except stripe.StripeError as exc:
        logger.warning(
            "Could not retrieve Stripe price %s to check the displayed price: %s (type=%s)",
            price_id, getattr(exc, "user_message", None) or str(exc), type(exc).__name__,
        )
        price, ttl = None, PRICE_ERROR_TTL_SECONDS
    with _lock:
        _cache[price_id] = (now + ttl, price)
    return price


def _decimals(currency: str) -> int:
    return 0 if currency.lower() in ZERO_DECIMAL_CURRENCIES else 2


def _to_major(minor: int, currency: str) -> Union[int, float]:
    value = minor / (10 ** _decimals(currency))
    return int(value) if float(value).is_integer() else round(value, 2)


@dataclass(frozen=True)
class DisplayPrice:
    amount: Union[int, float]
    currency: str
    #: Stripe recurring interval: "month" / "year".
    interval: str
    #: "stripe" when the amount came from (or was confirmed by) Stripe, else "config".
    source: str
    #: True when the configured display values disagreed with Stripe.
    mismatch: bool = False

    def as_dict(self) -> dict:
        return {
            "amount": self.amount,
            "currency": self.currency,
            "interval": self.interval,
            "verified": self.source == "stripe",
        }


def reconcile_display_price(plan: PlanConfig, checkout_interval: str) -> Optional[DisplayPrice]:
    """The price to display for ``plan`` billed ``checkout_interval``.

    ``None`` when there's nothing to show (no Stripe price and no configured
    display price for that interval).
    """
    expected_interval = CHECKOUT_INTERVALS[checkout_interval]
    configured = plan.display_prices.get(checkout_interval)
    currency = plan.currency
    fallback = (
        DisplayPrice(configured, currency, expected_interval, "config")
        if configured is not None else None
    )

    price_id = plan.price_for_interval(checkout_interval)
    if not price_id:
        # Not purchasable at this interval; only the monthly price is shown
        # without a Stripe price (e.g. local dev without Stripe).
        return fallback if checkout_interval == "monthly" else None

    price = retrieve_price(price_id)
    if price is None:
        return fallback

    unit_amount = _get(price, "unit_amount")
    stripe_currency = (_get(price, "currency") or "").lower()
    recurring = _get(price, "recurring") or {}
    stripe_interval = _get(recurring, "interval")
    interval_count = _get(recurring, "interval_count", 1) or 1
    if unit_amount is None or not stripe_currency:
        logger.error(
            "Stripe price %s for plan %s (%s) has no fixed unit_amount/currency; "
            "showing the configured display price, which cannot be verified",
            price_id, plan.id, checkout_interval,
        )
        return fallback

    stripe_amount = _to_major(int(unit_amount), stripe_currency)
    shown_interval = stripe_interval or expected_interval
    problems = []
    if configured is None:
        logger.info(
            "No display price configured for plan %s (%s); showing Stripe's %s %s",
            plan.id, checkout_interval, stripe_amount, stripe_currency,
        )
    elif int(unit_amount) != int(configured) * (10 ** _decimals(stripe_currency)):
        problems.append(f"amount {configured} != Stripe {stripe_amount}")
    if configured is not None and stripe_currency != currency:
        problems.append(f"currency {currency} != Stripe {stripe_currency}")
    if stripe_interval != expected_interval or int(interval_count) != 1:
        problems.append(
            f"interval {expected_interval} != Stripe {interval_count} x {stripe_interval}"
        )
    if problems:
        logger.error(
            "Displayed price for plan %s (%s) does not match Stripe price %s: %s. "
            "Serving Stripe's amount; fix BILLING_TEAM_PRICE_* / BILLING_CURRENCY "
            "or the Stripe price.",
            plan.id, checkout_interval, price_id, "; ".join(problems),
        )
    return DisplayPrice(stripe_amount, stripe_currency, shown_interval, "stripe", bool(problems))


__all__ = [
    "DisplayPrice",
    "PRICE_CACHE_TTL_SECONDS",
    "PRICE_ERROR_TTL_SECONDS",
    "clear_price_cache",
    "reconcile_display_price",
    "retrieve_price",
]
