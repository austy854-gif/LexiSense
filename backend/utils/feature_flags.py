"""Agentic feature flags shared by the API (route mounting) and the Celery
worker (task registration and beat schedule).

Every flag defaults to OFF. A group is "enabled" only when its flag is set to
1/true/yes/on. Keeping one source of truth means the worker can never run beat
jobs for features the API has not switched on.
"""
from __future__ import annotations

import os
from typing import Callable, Dict, Set

#: Feature group -> env flag gating it.
ROUTE_GROUP_FLAGS: Dict[str, str] = {
    "risk": "AGENTIC_PHASE1_ENABLED",
    "obligations": "AGENTIC_PHASE1_ENABLED",
    "agents": "AGENTIC_AGENTS_ENABLED",
    "intake": "AGENTIC_INTAKE_ENABLED",
    "playbooks": "AGENTIC_REDLINE_ENABLED",
    "redline": "AGENTIC_REDLINE_ENABLED",
}


def flag_enabled(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in ("1", "true", "yes", "on")


def enabled_groups(env_flag: Callable[[str], bool] = flag_enabled) -> Set[str]:
    """Feature groups whose flag is on (empty set by default)."""
    return {group for group, flag in ROUTE_GROUP_FLAGS.items() if env_flag(flag)}


#: Approval-request emails (one-click Approve/Reject links via Resend) and the
#: Resend delivery-status webhook. Default OFF, like the agentic flags.
APPROVAL_EMAILS_FLAG = "APPROVAL_EMAILS_ENABLED"


def approval_emails_enabled(env_flag: Callable[[str], bool] = flag_enabled) -> bool:
    return env_flag(APPROVAL_EMAILS_FLAG)


#: Billing UI (Billing page, nav entry, Upgrade buttons, price displays).
#: Default OFF while pricing is not final. Read at request time and exposed to
#: the frontend via GET /billing/config, so it can be flipped with an env change
#: and a restart -- no frontend rebuild. When off, user-facing billing messages
#: point to support instead of the hidden Billing page. Stripe webhooks and
#: entitlement enforcement are unaffected.
BILLING_UI_FLAG = "BILLING_UI_ENABLED"


def billing_ui_enabled(env_flag: Callable[[str], bool] = flag_enabled) -> bool:
    return env_flag(BILLING_UI_FLAG)
