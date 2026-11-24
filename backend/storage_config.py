"""Production fail-fast for durable object storage.

Dev and test may boot without AWS settings. Upload callers then fail closed.
Production, prod, and staging must not boot: a missing bucket or key would
otherwise log once and keep serving with storage silently unavailable.

The error names the missing variables and never includes their values.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

class StorageConfigError(RuntimeError):
    """Durable object storage is not configured and this process must not start."""


_PRODUCTION = frozenset({"production", "prod", "staging"})
_ACCESS_KEY = "AWS_ACCESS_KEY_ID"
_SECRET_KEY = "AWS_SECRET_ACCESS_KEY"
_BUCKET_KEYS = ("AWS_S3_BUCKET", "S3_BUCKET_NAME")


def _present(environ: Mapping[str, str], name: str) -> bool:
    raw = environ.get(name)
    return bool(raw and str(raw).strip())


def environment_name(environ: Mapping[str, str]) -> str:
    raw = environ.get("ENVIRONMENT")
    if raw is None:
        return "development"
    cleaned = str(raw).strip().lower()
    return cleaned or "development"


def missing_storage_settings(environ: Mapping[str, str]) -> list[str]:
    """Variable names that are unset. Bucket accepts the legacy alias too."""
    missing: list[str] = []
    if not _present(environ, _ACCESS_KEY):
        missing.append(_ACCESS_KEY)
    if not _present(environ, _SECRET_KEY):
        missing.append(_SECRET_KEY)
    if not any(_present(environ, name) for name in _BUCKET_KEYS):
        missing.append("AWS_S3_BUCKET")
    return missing


def require_durable_storage(environ: Optional[Mapping[str, str]] = None) -> None:
    """Raise when this process is production-like and object storage is unset.

    Development and test return without changing anything. A configured
    production process also returns.
    """
    env = os.environ if environ is None else environ
    missing = missing_storage_settings(env)
    if not missing:
        return
    name = environment_name(env)
    if name not in _PRODUCTION:
        return
    raise StorageConfigError(
        f"Durable object storage is required in {name}. Set {', '.join(missing)}."
    )
