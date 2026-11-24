"""Stripe checkout and webhook lockdown that does not import the app.

Production must refuse to boot without the Stripe secret key and the webhook
secret. Checkout and portal redirects must stay on the APP_URL origin. A
webhook may update only the organization that already owns the Stripe customer.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional
from urllib.parse import urlsplit

class StripeConfigError(RuntimeError):
    """Stripe is not configured and this process must not start."""


_PRODUCTION = frozenset({"production", "prod", "staging"})
_REQUIRED = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")
REDIRECT_REJECTED = "Redirect must stay on the application origin."


def _present(environ: Mapping[str, str], name: str) -> bool:
    raw = environ.get(name)
    return bool(raw and str(raw).strip())


def environment_name(environ: Mapping[str, str]) -> str:
    raw = environ.get("ENVIRONMENT")
    if raw is None or not str(raw).strip():
        return "development"
    return str(raw).strip().lower()


def require_stripe_config(environ: Optional[Mapping[str, str]] = None) -> None:
    """Raise when production-like and a Stripe secret is missing.

    The message lists variable names only.
    """
    env = os.environ if environ is None else environ
    name = environment_name(env)
    if name not in _PRODUCTION:
        return
    missing = [key for key in _REQUIRED if not _present(env, key)]
    if not missing:
        return
    raise StripeConfigError(
        f"Stripe is required in {name}. Set {', '.join(missing)}."
    )


def app_origin(app_url: Optional[str]) -> Optional[str]:
    """``scheme://host[:port]`` for an absolute http(s) URL, else None."""
    if app_url is None:
        return None
    raw = str(app_url).strip()
    if not raw or any(ch in raw for ch in "\\\r\n\t"):
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    host = parts.hostname.lower()
    if parts.port is None:
        netloc = host
    else:
        netloc = f"{host}:{parts.port}"
    return f"{parts.scheme.lower()}://{netloc}"


def redirect_allowed(url: Optional[str], app_url: Optional[str]) -> bool:
    """True when ``url`` is http(s) on the same origin as ``APP_URL``.

    An empty application origin fails closed. Userinfo, backslashes, and a
    different host or port are rejected. The path and query may vary.
    """
    origin = app_origin(app_url)
    if origin is None or url is None:
        return False
    candidate = str(url).strip()
    if not candidate or any(ch in candidate for ch in "\\\r\n\t @"):
        return False
    return app_origin(candidate) == origin


def resolve_webhook_org(
    metadata_org: Optional[str], customer_owner: Optional[str]
) -> Optional[str]:
    """Organization a Stripe object may update.

    When the customer is already stored, metadata must name that same
    organization. Metadata is used only when it agrees, or when the customer
    is not stored yet. A mismatch returns None so the caller skips the write.
    """
    meta = (metadata_org or "").strip() or None
    owner = (customer_owner or "").strip() or None
    if owner and meta and owner != meta:
        return None
    return meta or owner
