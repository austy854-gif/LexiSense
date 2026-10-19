"""Single source of truth for plan limits and Stripe price IDs.

Pricing is not final, so nothing in the billing / entitlement code hard-codes
a number: trial length, trial upload cap, member caps, display prices and the
Stripe price IDs a client may check out with all come from here, and every
value can be changed through environment variables without a code change.

Environment variables (all optional except the Stripe price ID in production):

``BILLING_TRIAL_DAYS``            in-app trial length in days (default 7)
``BILLING_TRIAL_UPLOAD_LIMIT``    contract uploads allowed during the trial (default 3)
``BILLING_TRIAL_MAX_MEMBERS``     member cap while on the trial (default 3)
``BILLING_TEAM_MAX_MEMBERS``      member cap on the paid Team plan (default 10)
``BILLING_TEAM_PRICE_MONTHLY``    display price for the Team plan (default 49)
``BILLING_PAST_DUE_GRACE_DAYS``   days a past-due subscriber keeps access after
                                  the first failed payment (default 7; 0 = block
                                  immediately)
``STRIPE_TEAM_PRICE_ID``          Stripe price for the Team plan, billed monthly
``STRIPE_TEAM_PRICE_ID_ANNUAL``   optional Stripe price for the Team plan, billed
                                  annually (``interval=annual`` at checkout)
``STRIPE_TEAM_PRICE_IDS``         optional comma-separated *additional* Stripe
                                  prices that still grant the Team plan to
                                  existing subscribers (e.g. legacy / CAD prices).
                                  They cannot be chosen at checkout.
``BILLING_TEAM_PRICE_ANNUAL``     optional display price for annual billing
``BILLING_CURRENCY``              ISO currency of the display prices (default usd)

The client never sends a Stripe price ID: checkout takes ``planId`` +
``interval`` and the server maps them to a price here. A subscription whose
price is not listed here grants no paid access.

A member cap of ``0`` or less means "unlimited".

Values are read on every call (cheap) so tests and a config reload pick up
changes without re-importing modules.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, Optional, Tuple

from utils.env import get_env

logger = logging.getLogger(__name__)

#: Plan id of the (currently only) paid plan; the default checkout ``planId``.
#: (An org whose stored Stripe price is unknown gets NO paid plan -- there is no
#: fallback to this plan's limits.)
DEFAULT_PAID_PLAN_ID = "team"

_DEFAULTS = {
    "BILLING_TRIAL_DAYS": 7,
    "BILLING_TRIAL_UPLOAD_LIMIT": 3,
    "BILLING_TRIAL_MAX_MEMBERS": 3,
    "BILLING_TEAM_MAX_MEMBERS": 10,
    "BILLING_TEAM_PRICE_MONTHLY": 49,
    "BILLING_PAST_DUE_GRACE_DAYS": 7,
}


def _env_int(name: str, default: int, minimum: Optional[int] = None) -> int:
    raw = get_env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.error("%s=%r is not an integer; using default %s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.error("%s=%s is below the minimum %s; using default %s", name, value, minimum, default)
        return default
    return value


def _limit(value: int) -> Optional[int]:
    """Normalise a cap: ``<= 0`` means unlimited (``None``)."""
    return value if value > 0 else None


@dataclass(frozen=True)
class TrialConfig:
    days: int
    upload_limit: int
    max_members: Optional[int]


#: Checkout intervals a client may ask for -> Stripe recurring interval.
CHECKOUT_INTERVALS: Dict[str, str] = {"monthly": "month", "annual": "year"}


@dataclass(frozen=True)
class PlanConfig:
    id: str
    name: str
    price_monthly: int
    interval: str
    #: Every Stripe price ID that grants this plan (checkout prices + extras).
    stripe_price_ids: Tuple[str, ...] = field(default_factory=tuple)
    max_members: Optional[int] = None
    #: ``None`` = unlimited uploads.
    max_uploads: Optional[int] = None
    #: Checkout interval ("monthly" / "annual") -> Stripe price ID.
    interval_prices: Dict[str, str] = field(default_factory=dict)
    #: Display prices per checkout interval (whole currency units).
    display_prices: Dict[str, Optional[int]] = field(default_factory=dict)
    currency: str = "usd"

    @property
    def default_price_id(self) -> Optional[str]:
        return self.interval_prices.get("monthly")

    def price_for_interval(self, interval: str) -> Optional[str]:
        return self.interval_prices.get(interval)


def get_trial_config() -> TrialConfig:
    return TrialConfig(
        days=_env_int("BILLING_TRIAL_DAYS", _DEFAULTS["BILLING_TRIAL_DAYS"], minimum=0),
        upload_limit=_env_int(
            "BILLING_TRIAL_UPLOAD_LIMIT", _DEFAULTS["BILLING_TRIAL_UPLOAD_LIMIT"], minimum=0
        ),
        max_members=_limit(
            _env_int("BILLING_TRIAL_MAX_MEMBERS", _DEFAULTS["BILLING_TRIAL_MAX_MEMBERS"])
        ),
    )


def get_past_due_grace_period() -> timedelta:
    """How long a past-due subscriber keeps access after payment first failed."""
    days = _env_int(
        "BILLING_PAST_DUE_GRACE_DAYS", _DEFAULTS["BILLING_PAST_DUE_GRACE_DAYS"], minimum=0
    )
    return timedelta(days=days)


def _team_interval_prices() -> Dict[str, str]:
    prices = {}
    monthly = (get_env("STRIPE_TEAM_PRICE_ID") or "").strip()
    annual = (get_env("STRIPE_TEAM_PRICE_ID_ANNUAL") or "").strip()
    if monthly:
        prices["monthly"] = monthly
    if annual:
        prices["annual"] = annual
    return prices


def _team_price_ids(interval_prices: Dict[str, str]) -> Tuple[str, ...]:
    ids = list(dict.fromkeys(interval_prices.values()))
    extra = get_env("STRIPE_TEAM_PRICE_IDS") or ""
    for pid in extra.split(","):
        pid = pid.strip()
        if pid and pid not in ids:
            ids.append(pid)
    return tuple(ids)


def get_paid_plans() -> Dict[str, PlanConfig]:
    """All purchasable plans, keyed by plan id."""
    interval_prices = _team_interval_prices()
    monthly = _env_int(
        "BILLING_TEAM_PRICE_MONTHLY", _DEFAULTS["BILLING_TEAM_PRICE_MONTHLY"], minimum=0
    )
    annual_raw = get_env("BILLING_TEAM_PRICE_ANNUAL")
    annual = _env_int("BILLING_TEAM_PRICE_ANNUAL", 0, minimum=0) if annual_raw else None
    team = PlanConfig(
        id=DEFAULT_PAID_PLAN_ID,
        name="Team Plan",
        price_monthly=monthly,
        interval="month",
        stripe_price_ids=_team_price_ids(interval_prices),
        max_members=_limit(
            _env_int("BILLING_TEAM_MAX_MEMBERS", _DEFAULTS["BILLING_TEAM_MAX_MEMBERS"])
        ),
        max_uploads=None,
        interval_prices=interval_prices,
        display_prices={"monthly": monthly, "annual": annual},
        currency=(get_env("BILLING_CURRENCY") or "usd").strip().lower(),
    )
    return {team.id: team}


def get_paid_plan(plan_id: Optional[str]) -> Optional[PlanConfig]:
    if not plan_id:
        return None
    return get_paid_plans().get(plan_id)


def plan_for_price_id(price_id: Optional[str]) -> Optional[PlanConfig]:
    """Return the plan a Stripe price ID belongs to, or ``None`` if not allowed."""
    if not price_id:
        return None
    for plan in get_paid_plans().values():
        if price_id in plan.stripe_price_ids:
            return plan
    return None


__all__ = [
    "CHECKOUT_INTERVALS",
    "DEFAULT_PAID_PLAN_ID",
    "PlanConfig",
    "TrialConfig",
    "get_trial_config",
    "get_past_due_grace_period",
    "get_paid_plans",
    "get_paid_plan",
    "plan_for_price_id",
]
