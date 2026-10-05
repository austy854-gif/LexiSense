#!/usr/bin/env python3
"""Independent harness proving the seed script's ``ContractTemplate`` handling.

PR #1 blocker 1 was: ``backend/seed_database.py`` builds every entry of
``DEFAULT_TEMPLATES`` with ``ContractTemplate(**template_data)``, but the
entries never supplied ``organizationId`` / ``createdBy`` -- both of which were
**required** fields on the ``ContractTemplate`` model.  Every run therefore died
with a ``pydantic.ValidationError`` before a single template was written.

This harness reproduces that crash against the *unpatched* model, proves every
default template now materialises against the *current* model, and (when a local
MongoDB is reachable) runs the real ``seed_database.seed_database()`` coroutine
against a scratch database.

It needs no network access and no pytest:

    python3 backend/scripts/seed_harness.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ``utils.auth`` raises at import time when JWT_SECRET is missing; the seed
# module imports it transitively, so set a dummy value before importing.
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "lexisense_seed_harness")

import pydantic  # noqa: E402
from pydantic import BaseModel, Field, ValidationError  # noqa: E402

from models.template import ContractTemplate  # noqa: E402


def _logs() -> list[str]:
    return []


def run_checks(log: list[str] | None = None) -> dict:
    """Run the offline checks and return a result dict (also usable from tests)."""
    from seed_database import DEFAULT_TEMPLATES

    log = log if log is not None else _logs()
    result: dict = {
        "pydantic": getattr(pydantic, "VERSION", "unknown"),
        "template_count": len(DEFAULT_TEMPLATES),
        "old_model_crashes": False,
        "model_materialises_all": False,
        "failed_templates": [],
        "missing_fields": [],
    }

    # 1. Reproduce the original crash against a faithful model of the UNPATCHED
    #    ContractTemplate (organizationId / createdBy required, as they were).
    class UnpatchedContractTemplate(BaseModel):
        model_config = {"extra": "ignore"}
        name: str
        description: str | None = None
        contractType: str = "General"
        content: str
        fields: list = Field(default_factory=list)
        tags: list = Field(default_factory=list)
        isPublic: bool = False
        organizationId: str  # required -> was the source of the crash
        createdBy: str       # required -> was the source of the crash

    try:
        for entry in DEFAULT_TEMPLATES:
            UnpatchedContractTemplate(**entry)
    except ValidationError as exc:
        result["old_model_crashes"] = True
        result["missing_fields"] = sorted(
            {str(err["loc"][0]) for err in exc.errors() if err.get("loc")}
        )
        log.append(f"PASS  unpatched model reproduces the crash ({exc.error_count()} error(s))")
    else:
        log.append("FAIL  unpatched model unexpectedly accepted the entries")

    # 2. Every default template must now materialise against the current model,
    #    carrying non-empty ownership. (``isDefault`` is deliberately NOT a model
    #    field -- extra="ignore" drops it -- so it is asserted on the persisted
    #    document in the live check below / by seed_database().)
    failures: list[str] = []
    for entry in DEFAULT_TEMPLATES:
        try:
            template = ContractTemplate(**entry)
        except ValidationError as exc:
            failures.append(f"{entry.get('name')!r}: {exc}")
            continue
        if not getattr(template, "organizationId", None):
            failures.append(f"{entry.get('name')!r}: organizationId empty")
        if not getattr(template, "createdBy", None):
            failures.append(f"{entry.get('name')!r}: createdBy empty")
    result["failed_templates"] = failures
    result["model_materialises_all"] = not failures
    log.append(
        "PASS  all templates materialise with non-empty ownership"
        if not failures
        else f"FAIL  {len(failures)} template(s) rejected"
    )
    return result


async def check_live_seed(log: list[str]) -> dict:
    """Run the real seed coroutine against a scratch DB, if Mongo is reachable."""
    outcome = {
        "ran": False,
        "inserted": 0,
        "attributed": 0,
        "attempts": 0,
        "second_run_skipped": False,
        "error": None,
    }
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except Exception as exc:  # pragma: no cover - motor is a runtime dep
        outcome["error"] = f"motor unavailable: {exc}"
        return outcome

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "lexisense_seed_harness")
    template_count = len(run_checks([]))

    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=2000)
    try:
        await client.admin.command("ping")
    except Exception as exc:
        outcome["error"] = f"mongo unreachable: {exc}"
        client.close()
        log.append("SKIP  local MongoDB unreachable - live seed check skipped")
        return outcome

    db = client[db_name]
    try:
        await db.templates.delete_many({})
        import importlib

        import seed_database as seed_module

        importlib.reload(seed_module)
        await seed_module.seed_database()
        outcome["ran"] = True
        outcome["attempts"] = await db.templates.count_documents({})
        outcome["inserted"] = await db.templates.count_documents({"isDefault": True})
        outcome["attributed"] = await db.templates.count_documents(
            {"isDefault": True, "organizationId": "system", "createdBy": "system"}
        )

        # A second run must be idempotent (the isDefault guard must match).
        await seed_module.seed_database()
        outcome["second_run_skipped"] = (
            await db.templates.count_documents({"isDefault": True}) == outcome["inserted"]
        )
        log.append(
            f"PASS  live seed inserted {outcome['inserted']} templates "
            f"(idempotent re-run: {outcome['second_run_skipped']})"
        )
    except Exception as exc:  # pragma: no cover
        outcome["error"] = repr(exc)
        log.append(f"FAIL  live seed raised: {exc!r}")
    finally:
        client.close()
    outcome["template_count"] = template_count
    return outcome


async def _main() -> int:
    log: list[str] = []
    print("== LexiSense seed harness (PR #1 blocker 1) ==")
    result = run_checks(log)
    live = await check_live_seed(log)
    result["live"] = live
    for line in log:
        print("  " + line)
    ok = result["old_model_crashes"] and result["model_materialises_all"]
    if live["ran"]:
        count = result["template_count"]
        if live["attempts"] != count or live["inserted"] != count:
            ok = False
        if live["attributed"] != count:
            ok = False
        if not live["second_run_skipped"]:
            ok = False
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
