"""Whether an organisation may upload, or is read-only after a failed payment.

Paid access is ``team`` + ``active`` (or a Stripe ``trialing`` subscription).
``past_due`` keeps upload access for ``BILLING_GRACE_DAYS`` (default 7), measured
from ``pastDueSince`` (or ``updatedAt`` for records written before that field
existed). After the grace window, and for any cancelled subscription, uploads
are rejected with a billing message — never the free-trial copy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from utils.env import get_env

READ_ONLY_MESSAGE = (
    "Your subscription needs attention. This workspace is read-only until billing is updated."
)

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
        since = _parse_dt(billing.get("pastDueSince")) or _parse_dt(billing.get("updatedAt")) or now
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
    if not (billing.get("subscriptionTier") == "team" or billing.get("stripeCustomerId")):
        return None
    since = _parse_dt(billing.get("pastDueSince")) or _parse_dt(billing.get("updatedAt"))
    if since is None:
        return None
    return (since + grace_window()).isoformat()
