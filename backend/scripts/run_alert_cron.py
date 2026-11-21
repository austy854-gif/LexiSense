#!/usr/bin/env python3
"""Run expiration alerts once, then exit.

Railway cron (one service, not a background worker, not an in-process scheduler):

    python -m scripts.run_alert_cron

Working directory is the backend root (the image ``WORKDIR`` is ``/app``).
The scan claims each (contract, window, recipient) once, so a second run is
a no-op for mail that already went out. A Mongo lease stops two overlapping
crons from scanning together. A crashed run can be claimed again after the
lease expires.

Optional ``HEALTHCHECKS_URL_CRON`` is pinged on start, success, and failure.
The URL is never logged. A failed ping does not change the scan result.
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.request import Request, urlopen

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

logger = logging.getLogger("alert_cron")

LOCK_ID = "expiration_alerts"
LEASE = timedelta(minutes=30)
_EPOCH = "1970-01-01T00:00:00+00:00"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def lock_is_free(locked_until: Optional[str], now: datetime) -> bool:
    """True when no lease is held at ``now``.

    ``locked_until`` is the ISO timestamp this process writes. Empty means free.
    """
    if locked_until is None or not str(locked_until).strip():
        return True
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return str(locked_until) <= now.astimezone(timezone.utc).isoformat()


def healthcheck_urls(base: Optional[str]) -> dict:
    """Healthchecks.io start/success/fail URLs, or empty when unset."""
    if base is None:
        return {}
    cleaned = str(base).strip().rstrip("/")
    if not cleaned:
        return {}
    return {"start": cleaned + "/start", "success": cleaned, "fail": cleaned + "/fail"}


def make_ping(base: Optional[str]) -> Callable[[str], None]:
    """Return a pinger. Missing URL and network errors are non-fatal."""
    urls = healthcheck_urls(base)

    def ping(kind: str) -> None:
        url = urls.get(kind)
        if not url:
            return
        try:
            request = Request(url, method="GET")
            with urlopen(request, timeout=5) as response:
                response.read(64)
        except Exception as exc:
            logger.warning(
                "Alert cron healthcheck ping failed: %s", type(exc).__name__
            )

    return ping


async def acquire_lock(db, now: Optional[datetime] = None) -> Optional[str]:
    """Take the single-run lease. None means another run still holds it."""
    from pymongo.errors import DuplicateKeyError

    now = now or _utcnow()
    token = uuid.uuid4().hex
    until = (now + LEASE).isoformat()
    try:
        await db.cron_locks.insert_one(
            {"_id": LOCK_ID, "token": token, "lockedUntil": until}
        )
        return token
    except DuplicateKeyError:
        pass
    updated = await db.cron_locks.update_one(
        {"_id": LOCK_ID, "lockedUntil": {"$lte": now.isoformat()}},
        {"$set": {"token": token, "lockedUntil": until}},
    )
    if updated.modified_count == 1:
        return token
    return None


async def release_lock(db, token: str) -> None:
    """Drop the lease so the next scheduled run can start immediately."""
    await db.cron_locks.update_one(
        {"_id": LOCK_ID, "token": token},
        {"$set": {"lockedUntil": _EPOCH}},
    )


async def run_once(
    *,
    acquire: Callable[[], Awaitable[Optional[str]]],
    release: Callable[[str], Awaitable[None]],
    scan: Callable[[], Awaitable[None]],
    ping: Callable[[str], None],
) -> int:
    """Scan once. Exit 0 on success or when another run holds the lease.

    Exit 1 when the scan raises. The lease is released either way so a failed
    run can be retried. The scan itself is idempotent.
    """
    token = await acquire()
    if not token:
        logger.info("Alert cron already running; this start is a no-op")
        return 0
    ping("start")
    try:
        await scan()
    except Exception:
        logger.exception("Alert cron scan failed")
        ping("fail")
        return 1
    else:
        ping("success")
        return 0
    finally:
        await release(token)


async def ensure_alert_index_once(db) -> None:
    """Create the unique alert index once. Raise if it is not ready to enforce."""
    from services.alert_service import alert_idempotency_index_ready
    from utils.database import INDEX_SPECIFICATION

    spec = next(
        item
        for item in INDEX_SPECIFICATION["expiration_alerts"]
        if item["name"] == "uq_alerts_idempotency"
    )
    options = {key: value for key, value in spec.items() if key != "keys"}
    await db.expiration_alerts.create_index(spec["keys"], **options)
    if not await alert_idempotency_index_ready(db):
        raise RuntimeError("Alert idempotency index is not ready")


async def _amain() -> int:
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient

    from services.alert_service import scan_expiration_alerts
    from utils.env import get_database_url, get_env

    load_dotenv(BACKEND_DIR / ".env")
    ping = make_ping(get_env("HEALTHCHECKS_URL_CRON"))
    url = get_database_url()
    if not url:
        logger.error("DATABASE_URL or MONGO_URL is required")
        ping("fail")
        return 1
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
    database = client[get_env("DB_NAME") or "lexisense"]

    async def acquire() -> Optional[str]:
        return await acquire_lock(database)

    async def release(token: str) -> None:
        await release_lock(database, token)

    async def scan() -> None:
        await ensure_alert_index_once(database)
        await scan_expiration_alerts(database)

    try:
        return await run_once(acquire=acquire, release=release, scan=scan, ping=ping)
    finally:
        client.close()


def main() -> int:
    import asyncio

    logging.basicConfig(level=logging.INFO)
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
