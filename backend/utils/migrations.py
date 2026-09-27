"""Lightweight MongoDB migration runner.

Runs idempotent, versioned migrations at startup. Each migration records
itself in the ``schema_migrations`` collection so it runs at most once per
database, regardless of how many app instances start up concurrently
(the record uses a unique index on ``version``).

Usage:
    from utils.migrations import run_migrations
    await run_migrations(db)

Add new migrations by appending to the ``MIGRATIONS`` list. Each entry is
a dict with keys ``version`` (int), ``name`` (str) and ``run`` (async
function that accepts the Motor db handle). Migrations MUST be idempotent
-- they may be re-executed against a partially applied database during
recovery.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, TypedDict

logger = logging.getLogger(__name__)


class Migration(TypedDict):
    version: int
    name: str
    run: Callable[[object], Awaitable[None]]


# ---------------------------------------------------------------------------
# Migration bodies
# ---------------------------------------------------------------------------

async def _m001_backfill_billing_trial_limits(db) -> None:
    """Ensure every billing record has trialContractsLimit and trialContractsUsed.

    Older accounts created before Gate 4 may be missing these fields, which
    breaks the atomic-reservation ``$expr`` filter.
    """
    await db.organization_billing.update_many(
        {"trialContractsLimit": {"$exists": False}},
        {"$set": {"trialContractsLimit": 3}},
    )
    await db.organization_billing.update_many(
        {"trialContractsUsed": {"$exists": False}},
        {"$set": {"trialContractsUsed": 0}},
    )


async def _m002_add_ai_analysis_status(db) -> None:
    """Backfill aiAnalysisStatus on existing contracts.

    Contracts uploaded before Gate 1 had no explicit success/failure marker.
    Anything with a non-null aiAnalysis is marked ``success``, otherwise ``unknown``.
    """
    await db.contracts.update_many(
        {"aiAnalysisStatus": {"$exists": False}, "aiAnalysis": {"$ne": None}},
        {"$set": {"aiAnalysisStatus": "success"}},
    )
    await db.contracts.update_many(
        {"aiAnalysisStatus": {"$exists": False}},
        {"$set": {"aiAnalysisStatus": "unknown"}},
    )


# ---------------------------------------------------------------------------
# Migration registry -- append-only, numbered.
# ---------------------------------------------------------------------------

MIGRATIONS: List[Migration] = [
    {"version": 1, "name": "backfill_billing_trial_limits", "run": _m001_backfill_billing_trial_limits},
    {"version": 2, "name": "add_ai_analysis_status", "run": _m002_add_ai_analysis_status},
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_migrations(db) -> None:
    """Apply any pending migrations. Idempotent and safe to run concurrently."""
    # Ensure the ledger collection has a unique index on version so parallel
    # workers cannot double-apply a migration.
    await db.schema_migrations.create_index("version", unique=True)

    applied_versions = set()
    async for doc in db.schema_migrations.find({}, {"version": 1, "_id": 0}):
        applied_versions.add(doc["version"])

    for migration in sorted(MIGRATIONS, key=lambda m: m["version"]):
        version = migration["version"]
        name = migration["name"]
        if version in applied_versions:
            continue

        logger.info("Applying migration %03d: %s", version, name)
        try:
            await migration["run"](db)
        except Exception as exc:
            logger.exception("Migration %03d (%s) failed: %s", version, name, exc)
            # Do not record the migration -- next startup will retry.
            raise

        try:
            await db.schema_migrations.insert_one({
                "version": version,
                "name": name,
                "appliedAt": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            # Duplicate key means another worker beat us to it -- fine.
            logger.info("Migration %03d already recorded by another worker", version)

        logger.info("Migration %03d applied", version)
