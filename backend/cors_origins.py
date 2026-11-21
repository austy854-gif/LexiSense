"""Exact CORS origin allow-list.

``CORS_ORIGINS`` is a comma-separated list of origins. A wildcard (``*`` as an
entry, or ``*`` inside an origin) is refused. The API sends
``Access-Control-Allow-Credentials: true``, and a wildcard origin would expose
credentialed responses to every site. ``server`` raises this at import, before
the framework or the database client is constructed.

This module stays outside ``utils`` so importing it does not run
``utils/__init__.py`` (which imports the auth stack).
"""

from __future__ import annotations

import os
from typing import Optional


class CorsOriginError(RuntimeError):
    """``CORS_ORIGINS`` contains a wildcard and the process must not start."""


def parse_cors_origins(raw: Optional[str]) -> list[str]:
    """Return exact origins from a comma-separated ``CORS_ORIGINS`` value.

    Empty or unset input returns an empty list. The caller warns, and browsers
    then block cross-origin calls. Any entry containing ``*`` raises
    :class:`CorsOriginError`. The rejected value is not included in the error.
    """
    if not raw:
        return []
    origins = [part.strip() for part in raw.split(",") if part.strip()]
    if any("*" in origin for origin in origins):
        raise CorsOriginError(
            "CORS_ORIGINS rejects wildcard origins. "
            "List exact origins; '*' is not allowed."
        )
    return origins


def load_cors_origins() -> list[str]:
    """Read ``CORS_ORIGINS`` the same way ``utils.env.get_env`` strips values.

    Surrounding whitespace and a trailing newline are removed. An unset or
    blank value is an empty allow-list. A wildcard still raises.
    """
    raw = os.environ.get("CORS_ORIGINS")
    if raw is None:
        return parse_cors_origins(None)
    raw = raw.strip()
    return parse_cors_origins(raw or None)
