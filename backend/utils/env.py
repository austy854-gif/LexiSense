"""Whitespace-safe environment variable access.

Values pasted into hosting dashboards (Railway, Render, ...) frequently carry
a trailing newline or stray spaces. Passed through verbatim they break things
in confusing ways: a ``DATABASE_URL`` ending in ``\\n`` made pymongo parse the
write concern as ``w='majority\\n'`` (error 79 ``UnknownReplWriteConcern``),
every index creation failed and ``/api/v1/ready`` stayed 503.

Use :func:`get_env` for connection strings, URLs, secrets and API keys, and
:func:`get_database_url` for the MongoDB connection string.
"""
import logging
import os
from typing import Optional, Set, overload

logger = logging.getLogger(__name__)

#: Variables already warned about. get_database_url() runs once per Celery task,
#: so the whitespace warning is emitted at most once per process per variable.
_whitespace_warned: Set[str] = set()


@overload
def get_env(name: str) -> Optional[str]:
    ...


@overload
def get_env(name: str, default: str) -> str:
    ...


@overload
def get_env(name: str, default: None) -> Optional[str]:
    ...


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Return ``os.environ[name]`` with leading/trailing whitespace removed.

    Strips spaces, tabs, ``\\n`` and ``\\r`` (so CRLF line endings are handled).
    An unset variable, or one that is empty after stripping, yields ``default``.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def get_database_url() -> Optional[str]:
    """MongoDB connection string: ``DATABASE_URL``, else ``MONGO_URL``, stripped.

    Logs a warning (never the value itself) when surrounding whitespace had to
    be removed, so a mis-pasted dashboard value is visible in the logs. The
    warning is emitted once per process per variable (Celery calls this for
    every task).
    """
    for name in ("DATABASE_URL", "MONGO_URL"):
        raw = os.environ.get(name)
        if raw is None:
            continue
        cleaned = raw.strip()
        if not cleaned:
            continue
        if cleaned != raw and name not in _whitespace_warned:
            _whitespace_warned.add(name)
            logger.warning(
                "%s contained leading/trailing whitespace (e.g. a trailing newline); "
                "it was stripped. Fix the value in the environment configuration.",
                name,
            )
        return cleaned
    return None
