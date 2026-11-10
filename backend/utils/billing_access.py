"""Whether an organisation may upload, or is read-only after a failed payment.

Paid access is ``team`` + ``active`` (or a Stripe ``trialing`` subscription).
``past_due`` keeps upload access for ``BILLING_GRACE_DAYS`` (default 7), measured
from ``pastDueSince`` (or ``updatedAt`` for records written before that field
existed). After the grace window, and for any cancelled subscription, uploads
are rejected with a billing message — never the free-trial copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from utils import plans as _plans
from utils.env import get_env
from utils.feature_flags import billing_ui_enabled

READ_ONLY_MESSAGE = "Your subscription needs attention. This workspace is read-only until billing is updated."

_ALLOW = "allow"
_TRIAL = "trial"
_READONLY = "readonly"


def grace_window() -> timedelta:
    try:
        days = int(get_env("BILLING_GRACE_DAYS", "7"))
    except (TypeError, ValueError):
        days = 7
    return timedelta(days=max(0, min(days, 60)))


def _parse_dt(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def upload_gate(billing: Optional[dict], now: Optional[datetime] = None) -> str:
    """Return ``allow`` (paid path), ``trial`` (use the trial counter), or ``readonly``."""
    billing = billing or {}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    tier = billing.get("subscriptionTier")
    status = billing.get("billingStatus")

    if status == "cancelled" or tier == "cancelled":
        return _READONLY
    if tier == "team" and status in ("active", "trialing"):
        return _ALLOW
    if status == "past_due" and (tier == "team" or billing.get("stripeCustomerId")):
        since = (
            _parse_dt(billing.get("pastDueSince"))
            or _parse_dt(billing.get("updatedAt"))
            or now
        )
        if now - since <= grace_window():
            return _ALLOW
        return _READONLY
    if status == "past_due":
        return _READONLY
    return _TRIAL


def grace_ends_at(billing: Optional[dict]) -> Optional[str]:
    """ISO timestamp when a past-due grace window ends, else ``None``."""
    billing = billing or {}
    if billing.get("billingStatus") != "past_due":
        return None
    if not (
        billing.get("subscriptionTier") == "team" or billing.get("stripeCustomerId")
    ):
        return None
    since = _parse_dt(billing.get("pastDueSince")) or _parse_dt(
        billing.get("updatedAt")
    )
    if since is None:
        return None
    return (since + grace_window()).isoformat()


# ---------------------------------------------------------------------------
# Entitlement: one decision for uploads, AI (analysis, chat, background work)
# and invitations. Ported from fix/billing-integrity (#78, B1/B2/B4/B5) and
# adapted to the Option B plan config in utils.plans.
# ---------------------------------------------------------------------------

ACCESS_PAID = "paid"
ACCESS_GRACE = "grace"
ACCESS_TRIAL = "trial"
ACCESS_TRIAL_EXPIRED = "trial_expired"
ACCESS_PAST_DUE = "past_due"  # read-only after the grace window
ACCESS_CANCELLED = "cancelled"
ACCESS_UNVERIFIED_PLAN = "unverified_plan"

#: States in which the workspace is read-only for every contract mutation.
#: Single source of truth for ``assert_org_writable`` and ``/billing/capabilities``.
#: ``trial_expired`` is deliberately absent: an expired trial only blocks new
#: uploads (enforced by the upload gate), existing contracts stay editable.
READ_ONLY_STATES = frozenset(
    {ACCESS_PAST_DUE, ACCESS_CANCELLED, ACCESS_UNVERIFIED_PLAN}
)

MSG_UNVERIFIED_PLAN = (
    "We couldn't verify your subscription plan. Please contact support to restore "
    "uploads and AI analysis."
)
_MESSAGES = {
    # state: (billing UI on, billing UI off -> point to support, never the hidden page)
    ACCESS_TRIAL_EXPIRED: (
        "Your free trial has ended. Upgrade to keep adding contracts.",
        "Your free trial has ended. Please contact support to continue using LexiSense.",
    ),
    ACCESS_PAST_DUE: (
        "Your last payment failed, so your account is read-only. Please update your payment "
        "method in the billing portal to restore uploads, AI analysis, chat and invitations.",
        "Your last payment failed, so your account is read-only. Please contact support to "
        "restore uploads, AI analysis, chat and invitations.",
    ),
    ACCESS_CANCELLED: (
        "Your subscription has been cancelled. Resubscribe on the Billing page to continue.",
        "Your subscription has been cancelled. Please contact support to continue using LexiSense.",
    ),
    ACCESS_UNVERIFIED_PLAN: (MSG_UNVERIFIED_PLAN, MSG_UNVERIFIED_PLAN),
}


def blocked_message(state: str) -> str:
    on, off = _MESSAGES.get(state, _MESSAGES[ACCESS_CANCELLED])
    return on if billing_ui_enabled() else off


def legacy_price_ids() -> frozenset:
    """Retired prices that still entitle existing subscribers (never purchasable)."""
    raw = get_env("STRIPE_LEGACY_PRICE_IDS", "") or ""
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


@dataclass(frozen=True)
class Entitlement:
    state: str
    plan_id: Optional[str]
    can_use_ai: bool
    max_members: Optional[int]
    message: Optional[str] = None

    @property
    def read_only(self) -> bool:
        return self.state == ACCESS_PAST_DUE

    @property
    def blocked(self) -> bool:
        return not self.can_use_ai


def _paid_plan_for(billing: dict):
    """Plan for the stored Stripe price; ``(None, False)`` when it isn't configured."""
    price_id = billing.get("stripePriceId")
    plan = _plans.plan_for_price_id(price_id)
    if plan is not None:
        return plan, True
    if price_id and price_id in legacy_price_ids():
        return _plans.get_paid_plan(_plans.DEFAULT_PAID_PLAN_ID), True
    return None, False


def compute_entitlement(
    billing: Optional[dict], now: Optional[datetime] = None
) -> Entitlement:
    """B1: active or trialing is paid. B2: past-due grace then read-only.
    B4: a subscription on a price that isn't configured grants nothing."""
    billing = billing or {}
    now = now or datetime.now(timezone.utc)
    gate = upload_gate(billing, now)
    trial_cap = _plans.trial_max_members()
    status = billing.get("billingStatus")

    if gate == _ALLOW:
        plan, known = _paid_plan_for(billing)
        if not known:
            return Entitlement(
                ACCESS_UNVERIFIED_PLAN,
                None,
                False,
                trial_cap,
                blocked_message(ACCESS_UNVERIFIED_PLAN),
            )
        state = ACCESS_GRACE if status == "past_due" else ACCESS_PAID
        return Entitlement(state, plan.id, True, plan.max_members)
    if gate == _READONLY:
        if status == "past_due" and billing.get("subscriptionTier") != "cancelled":
            plan, _ = _paid_plan_for(billing)
            cap = plan.max_members if plan else trial_cap
            return Entitlement(
                ACCESS_PAST_DUE,
                plan.id if plan else None,
                False,
                cap,
                blocked_message(ACCESS_PAST_DUE),
            )
        return Entitlement(
            ACCESS_CANCELLED, None, False, trial_cap, blocked_message(ACCESS_CANCELLED)
        )
    trial_end = _parse_dt(billing.get("trialEndDate"))
    if trial_end is not None and now > trial_end:
        return Entitlement(
            ACCESS_TRIAL_EXPIRED,
            None,
            False,
            trial_cap,
            blocked_message(ACCESS_TRIAL_EXPIRED),
        )
    return Entitlement(ACCESS_TRIAL, None, True, trial_cap)
