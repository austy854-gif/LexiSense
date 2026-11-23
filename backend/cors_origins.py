"""Exact CORS origin allow-list.

``CORS_ORIGINS`` is a comma-separated list of origins. Each entry must be a
well-formed ``http://`` or ``https://`` origin (scheme + host [+ port], with
no path, query, fragment, or userinfo). A wildcard (``*`` as an entry, or
``*`` inside an origin) is refused. The API sends
``Access-Control-Allow-Credentials: true``, and a wildcard origin would expose
credentialed responses to every site.

``server`` raises this at import, before the framework or the database client
is constructed. An empty allow-list is refused when ``ENVIRONMENT`` (trimmed,
lowercased) is ``production``; otherwise the caller is left with an empty
list and this module logs a warning.

This module stays outside ``utils`` so importing it does not run
``utils/__init__.py`` (which imports the auth stack).
"""

from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import SplitResult, urlsplit

logger = logging.getLogger(__name__)


class CorsOriginError(RuntimeError):
    """``CORS_ORIGINS`` is invalid and the process must not start."""


def _display_origin(origin: str) -> str:
    """Return *origin* for error messages, with userinfo redacted."""
    try:
        parts = urlsplit(origin)
    except ValueError:
        return origin
    if parts.username is None and parts.password is None:
        return origin
    host = parts.netloc.rsplit("@", 1)[-1]
    scheme = parts.scheme
    prefix = f"{scheme}://" if scheme else ""
    return f"{prefix}***@{host}"


def _has_forbidden_origin_chars(origin: str) -> bool:
    """True when *origin* has a wildcard, whitespace, or path-like characters."""
    if "*" in origin or any(c.isspace() for c in origin):
        return True
    _scheme, separator, rest = origin.partition("://")
    return separator != "://" or any(c in rest for c in "/?#@\\")


def _split_origin(origin: str) -> Optional[SplitResult]:
    try:
        parts = urlsplit(origin)
        # Accessing .port validates that an explicit port is in range.
        _ = parts.port
    except ValueError:
        return None
    return parts


def _is_well_formed_http_origin(origin: str) -> bool:
    """True when *origin* is ``http(s)://`` host [ ``:`` port ] only."""
    if _has_forbidden_origin_chars(origin):
        return False
    parts = _split_origin(origin)
    if parts is None:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if parts.path or parts.query or parts.fragment:
        return False
    if not parts.hostname or not parts.netloc or parts.netloc.endswith(":"):
        return False
    return True


def parse_cors_origins(raw: Optional[str]) -> list[str]:
    """Return exact origins from a comma-separated ``CORS_ORIGINS`` value.

    Empty or unset input returns an empty list. Surrounding whitespace is
    trimmed and empty entries (including those from trailing commas) are
    ignored. Any entry containing ``*``, or that is not a well-formed
    ``http://`` or ``https://`` origin, raises :class:`CorsOriginError`
    naming the rejected value (userinfo is redacted).
    """
    if not raw:
        return []
    origins = [part.strip() for part in raw.split(",") if part.strip()]
    checked: list[str] = []
    for origin in origins:
        if "*" in origin:
            raise CorsOriginError(
                "CORS_ORIGINS rejects wildcard origin "
                f"{_display_origin(origin)!r}. "
                "List exact http(s) origins; '*' is not allowed."
            )
        if not _is_well_formed_http_origin(origin):
            raise CorsOriginError(
                "CORS_ORIGINS rejects invalid origin "
                f"{_display_origin(origin)!r}. "
                "Each origin must be a well-formed http:// or https:// origin "
                "(scheme + host [+ port], with no path, query, fragment, or "
                "userinfo)."
            )
        checked.append(origin)
    return checked


def validate_cors_allow_list(
    origins: list[str], environment: Optional[str]
) -> list[str]:
    """Apply environment policy to a parsed CORS allow-list.

    An empty list in production (``ENVIRONMENT`` trimmed and lowercased)
    raises :class:`CorsOriginError`. In any other environment the empty
    list is returned and a warning is logged, matching the historical
    non-production behaviour.
    """
    if origins:
        return origins
    env = (environment or "").strip().lower()
    if env == "production":
        raise CorsOriginError(
            "CORS_ORIGINS is empty; set at least one exact http(s) origin "
            "when ENVIRONMENT=production."
        )
    logger.warning("CORS_ORIGINS is empty - no cross-origin requests will be allowed")
    return origins


def load_cors_origins() -> list[str]:
    """Read ``CORS_ORIGINS`` the same way ``utils.env.get_env`` strips values.

    Surrounding whitespace and a trailing newline are removed. An unset or
    blank value is an empty allow-list, which is then checked against
    ``ENVIRONMENT``. A wildcard or malformed origin still raises.
    """
    raw = os.environ.get("CORS_ORIGINS")
    if raw is not None:
        raw = raw.strip() or None
    origins = parse_cors_origins(raw)
    return validate_cors_allow_list(origins, os.environ.get("ENVIRONMENT"))
