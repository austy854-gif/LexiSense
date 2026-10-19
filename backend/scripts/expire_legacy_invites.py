#!/usr/bin/env python3
"""Expire pending invitations created before invite-token hashing shipped.

DRY RUN BY DEFAULT: prints counts only and writes nothing. Pass ``--apply``
to write. Output never includes tokens, hashes or email addresses.

    # what would be expired (cutoff = when migration 003 ran)
    DATABASE_URL=... DB_NAME=... python3 backend/scripts/expire_legacy_invites.py

    # explicit cutoff
    python3 backend/scripts/expire_legacy_invites.py --before 2026-10-18T07:00:00Z

    # actually expire them
    python3 backend/scripts/expire_legacy_invites.py --apply

Idempotent: only ``pending`` invites match, so re-running is a no-op.
Expired invites can be re-sent by inviting the same address again.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils.invite_expiry import (  # noqa: E402
    HASH_MIGRATION_VERSION,
    default_cutoff,
    expire_pending_invites_before,
    parse_iso,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--before", help="ISO-8601 cutoff; default: when migration 003 was applied")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    return parser


def _connect():
    from motor.motor_asyncio import AsyncIOMotorClient
    from utils.env import get_database_url, get_env

    url = get_database_url()
    if not url:
        raise SystemExit("DATABASE_URL (or MONGO_URL) must be set")
    name = get_env("DB_NAME")
    if not name:
        from pymongo.uri_parser import parse_uri
        name = parse_uri(url).get("database")
    if not name:
        raise SystemExit("DB_NAME must be set (or included in the connection string)")
    return AsyncIOMotorClient(url, appname="lexisense-expire-legacy-invites")[name]


async def run(db, before=None, apply=False, out=print) -> int:
    if before:
        try:
            cutoff = parse_iso(before)
        except ValueError:
            out(f"error: --before is not an ISO-8601 timestamp: {before!r}")
            return 2
    else:
        cutoff = await default_cutoff(db)
        if cutoff is None:
            out(f"error: migration {HASH_MIGRATION_VERSION:03d} has not run; pass --before explicitly")
            return 2

    report = await expire_pending_invites_before(db, cutoff, apply=apply)
    out(f"mode:            {'APPLY' if apply else 'DRY RUN (no changes written; use --apply)'}")
    out(f"cutoff:          {report.cutoff}")
    out(f"pending total:   {report.pending_total}")
    out(f"created before:  {report.matched} (incl. {report.unparseable} with unparseable createdAt)")
    out(f"expired now:     {report.expired}")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(_connect(), before=args.before, apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
