"""Expire pending invitations issued before invite-token hashing shipped.

Invitations created before migration 003 had their raw token stored (and,
when ``RESEND_API_KEY`` was unset, logged). Hashing them at rest does not
revoke links that may already sit in old logs, so admins can expire every
still-pending invite created before a cutoff. An expired invite is not
deleted: the address can simply be invited again (``POST /team/invite`` only
refuses while a *pending* invite exists), which issues a fresh token.

Entry points (both opt-in, neither runs by default):

* ``backend/scripts/expire_legacy_invites.py`` -- dry run unless ``--apply``.
* ``EXPIRE_LEGACY_INVITES_ON_STARTUP=true`` -- applied on each startup after
  migrations, always with the migration-003 cutoff, so it is idempotent and
  never touches invites issued after hashing shipped.

Output is counts only -- never tokens, hashes or email addresses.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from utils.env import get_env

logger = logging.getLogger(__name__)

HASH_MIGRATION_VERSION = 3
EXPIRY_REASON = "legacy_token_rotation"
STARTUP_FLAG = "EXPIRE_LEGACY_INVITES_ON_STARTUP"


@dataclass
class ExpiryReport:
    cutoff: str
    pending_total: int
    matched: int
    expired: int
    unparseable: int
    applied: bool


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; naive values are treated as UTC."""
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def default_cutoff(db) -> Optional[datetime]:
    """When migration 003 (token hashing) was applied, or ``None`` if it has not run."""
    record = await db.schema_migrations.find_one({"version": HASH_MIGRATION_VERSION}, {"_id": 0, "appliedAt": 1})
    if not record or not record.get("appliedAt"):
        return None
    return parse_iso(record["appliedAt"])


async def expire_pending_invites_before(db, cutoff: datetime, apply: bool = False) -> ExpiryReport:
    """Expire (when ``apply``) every pending invitation with ``createdAt < cutoff``.

    Idempotent: only ``pending`` records match, and each update is conditional
    on the record still being pending.
    """
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    pending_total = matched = expired = unparseable = 0
    ids = []
    async for doc in db.invitations.find({"status": "pending"}, {"_id": 0, "id": 1, "createdAt": 1}):
        pending_total += 1
        try:
            created = parse_iso(str(doc.get("createdAt") or ""))
        except ValueError:
            # Unknown age: treat as legacy, it cannot be shown to be newer.
            unparseable += 1
            created = None
        if created is None or created < cutoff:
            matched += 1
            ids.append(doc["id"])

    if apply:
        now = datetime.now(timezone.utc).isoformat()
        for invitation_id in ids:
            result = await db.invitations.update_one(
                {"id": invitation_id, "status": "pending"},
                {"$set": {"status": "expired", "expiresAt": now, "expiredAt": now, "expiredReason": EXPIRY_REASON}},
            )
            expired += result.modified_count

    return ExpiryReport(cutoff.isoformat(), pending_total, matched, expired, unparseable, apply)


def startup_flag_enabled() -> bool:
    return (get_env(STARTUP_FLAG, "false") or "").strip().lower() in ("1", "true", "yes", "on")


async def maybe_expire_on_startup(db) -> Optional[ExpiryReport]:
    """Run the expiry at startup only when the opt-in env flag is set (OFF by default)."""
    if not startup_flag_enabled():
        return None
    cutoff = await default_cutoff(db)
    if cutoff is None:
        logger.warning("%s set but migration %03d has not run; skipping", STARTUP_FLAG, HASH_MIGRATION_VERSION)
        return None
    report = await expire_pending_invites_before(db, cutoff, apply=True)
    logger.info(
        "Legacy invitation expiry applied (cutoff=%s pending=%d matched=%d expired=%d)",
        report.cutoff, report.pending_total, report.matched, report.expired,
    )
    return report
