"""Resolve webhook signing secrets from the environment.

The intake (inbound email) webhook and the delivery-status webhook are two
separate Resend webhooks, each with its own Svix signing secret, so they need
separate variables:

* ``RESEND_INTAKE_WEBHOOK_SECRET`` -- intake webhook (``/agentic/intake/webhook``)
* ``RESEND_WEBHOOK_SECRET``        -- delivery-status webhook

For backwards compatibility the intake webhook falls back to
``RESEND_WEBHOOK_SECRET`` (logging a one-time deprecation warning). Secret
values are never logged.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from utils.env import get_env

logger = logging.getLogger(__name__)

INTAKE_SECRET_VAR = "RESEND_INTAKE_WEBHOOK_SECRET"
LEGACY_SECRET_VAR = "RESEND_WEBHOOK_SECRET"

_warned = False
_warn_lock = threading.Lock()


def get_intake_webhook_secret() -> Optional[str]:
    """Secret for the intake webhook, or ``None`` when neither variable is set."""
    global _warned
    secret = get_env(INTAKE_SECRET_VAR)
    if secret:
        return secret
    legacy = get_env(LEGACY_SECRET_VAR)
    if legacy:
        with _warn_lock:
            if not _warned:
                _warned = True
                logger.warning(
                    "%s is not set; falling back to %s for the intake webhook. "
                    "This fallback is deprecated: set %s to the intake webhook's own signing secret.",
                    INTAKE_SECRET_VAR, LEGACY_SECRET_VAR, INTAKE_SECRET_VAR,
                )
        return legacy
    return None


def _reset_warning_for_tests() -> None:
    global _warned
    _warned = False
