"""Single source of truth for plan limits, Stripe price IDs and displayed prices.

Launch pricing (Option B): a trial plus Starter, Team and Business, each billed
monthly or annually. This module deliberately mirrors the names and env vars of
``utils/plans.py`` on ``feat/launch-pricing`` (a compatible subset) so the two
reconcile by taking that branch's version.

Stripe prices -- the ONLY prices checkout will accept (server-side allowlist):

``STRIPE_PRICE_STARTER_MONTHLY``   ``STRIPE_PRICE_STARTER_ANNUAL``
``STRIPE_PRICE_TEAM_MONTHLY``      ``STRIPE_PRICE_TEAM_ANNUAL``
``STRIPE_PRICE_BUSINESS_MONTHLY``  ``STRIPE_PRICE_BUSINESS_ANNUAL``

Member caps (``0`` = unlimited): ``BILLING_TRIAL_MAX_MEMBERS`` (3),
``BILLING_STARTER_MAX_MEMBERS`` (5), ``BILLING_TEAM_MAX_MEMBERS`` (15),
``BILLING_BUSINESS_MAX_MEMBERS`` (30).

Displayed prices are NOT hard-coded. ``/billing/plans`` shows the amount of the
same Stripe Price object checkout charges (see ``services.stripe_prices``).
``BILLING_<PLAN>_PRICE_MONTHLY`` / ``_ANNUAL`` are optional fallbacks used only
when Stripe can't be reached; a mismatch with Stripe is logged as an error.

Values are read on every call so tests and config changes need no re-import.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Tuple

from utils.env import get_env

logger = logging.getLogger(__name__)

PAID_PLAN_IDS: Tuple[str, ...] = ("starter", "team", "business")
TRIAL_PLAN_ID = "trial"
DEFAULT_PAID_PLAN_ID = "team"

#: Checkout interval -> Stripe recurring interval.
CHECKOUT_INTERVALS: Dict[str, str] = {"monthly": "month", "annual": "year"}
_PRICE_ENV_SUFFIX = {"monthly": "MONTHLY", "annual": "ANNUAL"}

_NAMES = {"starter": "Starter", "team": "Team", "business": "Business"}
#: Option B seat caps (approved Oct 19, 2026). Overridable by env.
_MEMBER_DEFAULTS = {"trial": 3, "starter": 5, "team": 15, "business": 30}


def stripe_price_env_var(plan_id: str, interval: str) -> str:
    return f"STRIPE_PRICE_{plan_id.upper()}_{_PRICE_ENV_SUFFIX[interval]}"


STRIPE_PRICE_ENV_VARS: Tuple[str, ...] = tuple(
    stripe_price_env_var(p, i) for p in PAID_PLAN_IDS for i in CHECKOUT_INTERVALS
)


def _env_int(name: str, default: Optional[int]) -> Optional[int]:
    raw = (get_env(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.error("%s=%r is not an integer; using default %s", name, raw, default)
        return default
    if value < 0:
        logger.error("%s=%s is negative; using default %s", name, value, default)
        return default
    return value


def _limit(value: Optional[int]) -> Optional[int]:
    return value if value and value > 0 else None


@dataclass(frozen=True)
class PlanConfig:
    id: str
    name: str
    max_members: Optional[int]
    #: Checkout interval -> Stripe price ID (only configured ones).
    interval_prices: Dict[str, str] = field(default_factory=dict)
    #: Fallback display amounts (whole USD) per interval, from env only.
    fallback_prices: Dict[str, Optional[int]] = field(default_factory=dict)

    @property
    def stripe_price_ids(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(self.interval_prices.values()))

    def price_for_interval(self, interval: str) -> Optional[str]:
        return self.interval_prices.get(interval)


def trial_max_members() -> Optional[int]:
    return _limit(_env_int("BILLING_TRIAL_MAX_MEMBERS", _MEMBER_DEFAULTS["trial"]))


def _build_plan(plan_id: str) -> PlanConfig:
    prices = {}
    for interval in CHECKOUT_INTERVALS:
        pid = (get_env(stripe_price_env_var(plan_id, interval)) or "").strip()
        if pid:
            prices[interval] = pid
    env = f"BILLING_{plan_id.upper()}_"
    return PlanConfig(
        id=plan_id,
        name=_NAMES[plan_id],
        max_members=_limit(_env_int(env + "MAX_MEMBERS", _MEMBER_DEFAULTS[plan_id])),
        interval_prices=prices,
        fallback_prices={
            i: _env_int(env + "PRICE_" + _PRICE_ENV_SUFFIX[i], None)
            for i in CHECKOUT_INTERVALS
        },
    )


def get_paid_plans() -> Dict[str, PlanConfig]:
    return {pid: _build_plan(pid) for pid in PAID_PLAN_IDS}


def get_paid_plan(plan_id: Optional[str]) -> Optional[PlanConfig]:
    return get_paid_plans().get(plan_id) if plan_id else None


def plan_for_price_id(price_id: Optional[str]) -> Optional[PlanConfig]:
    """The plan a Stripe price belongs to, or ``None`` if it isn't allow-listed."""
    if not price_id:
        return None
    for plan in get_paid_plans().values():
        if price_id in plan.stripe_price_ids:
            return plan
    return None


def plan_and_interval_for_price_id(
    price_id: Optional[str],
) -> Tuple[Optional[PlanConfig], Optional[str]]:
    plan = plan_for_price_id(price_id)
    if plan is None:
        return None, None
    for interval, pid in plan.interval_prices.items():
        if pid == price_id:
            return plan, interval
    return plan, None


def allowed_price_ids() -> FrozenSet[str]:
    """Every Stripe price checkout may use: exactly the six STRIPE_PRICE_* vars."""
    return frozenset(
        pid for plan in get_paid_plans().values() for pid in plan.stripe_price_ids
    )


#: Stripe ``tax_behavior`` of the six prices. All six are created with
#: ``tax_behavior=inclusive`` (STRIPE_SETUP.md, decided Oct 20, 2026), so the
#: listed amount is what the customer pays. The Stripe Price object wins when it
#: can be read; this value is the fallback and is checked against it.
TAX_BEHAVIORS = ("inclusive", "exclusive", "unspecified")


def tax_behavior() -> str:
    value = (
        (get_env("BILLING_TAX_BEHAVIOR", "inclusive") or "inclusive").strip().lower()
    )
    if value not in TAX_BEHAVIORS:
        logger.error("BILLING_TAX_BEHAVIOR=%r is invalid; using 'inclusive'", value)
        return "inclusive"
    return value


def price_label(currency: str, behavior: str) -> str:
    """Short label shown next to a price, e.g. ``USD, tax included``."""
    cur = (currency or "usd").upper()
    if behavior == "inclusive":
        return f"{cur}, tax included"
    if behavior == "exclusive":
        return f"{cur}, plus applicable tax"
    return cur
