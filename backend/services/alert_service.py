"""Organization-scoped expiration/renewal alert scan used by API and scheduler.

Delivery semantics
------------------
* One alert record per idempotency key
  ``{org}:{contract}:expiration:{days}:{recipient}`` (unique, partial index).
* A scan *claims* a record atomically (``status -> "sending"`` with a fresh
  ``claimToken``) and only the holder of that token sends. ``sending`` records
  are not claimable again until their lease expires, so concurrent scans in
  several workers/processes cannot double-send.
* Every provider attempt is persisted (``$inc attempts``) *before* the
  provider call, so ``MAX_ATTEMPTS`` is enforced across scheduler runs and
  process restarts. There is no in-process retry loop and no sleeping.
* Failed records get an exponential ``nextAttemptAt`` and are retried by a
  separate pass that does not depend on today's expiry-date selection.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.email_service import send_expiration_alert

logger = logging.getLogger(__name__)
DEFAULT_ALERT_DAYS = (30, 14, 7, 1)
MAX_ATTEMPTS = 3
#: A ``sending`` claim older than this is assumed abandoned (worker crashed
#: mid-send) and may be reclaimed if attempts remain.
CLAIM_LEASE = timedelta(minutes=15)
RETRY_BASE_DELAY = timedelta(minutes=5)


async def alert_idempotency_index_ready(db) -> bool:
    """True when ``uq_alerts_idempotency`` exists and is unique."""
    try:
        info = await db.expiration_alerts.index_information()
    except Exception:
        logger.exception("Could not read expiration_alerts indexes")
        return False
    spec = info.get("uq_alerts_idempotency") or {}
    return bool(spec.get("unique"))


async def ensure_alert_idempotency_index(
    db, on_ready, *, initial_delay: float = 1.0, max_delay: float = 60.0, sleep=None
) -> None:
    """Create and verify ``uq_alerts_idempotency`` until it succeeds.

    Used when startup could not confirm the index (e.g. a transient MongoDB
    failure) so alerts recover without a process restart. ``on_ready`` is only
    called after verification succeeds.
    """
    import asyncio

    from utils.database import INDEX_SPECIFICATION

    sleep = sleep or asyncio.sleep
    spec = next(
        s
        for s in INDEX_SPECIFICATION["expiration_alerts"]
        if s["name"] == "uq_alerts_idempotency"
    )
    options = {k: v for k, v in spec.items() if k != "keys"}
    delay = initial_delay
    while True:
        try:
            await db.expiration_alerts.create_index(spec["keys"], **options)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Alert index creation retry failed (error_type=%s)", type(exc).__name__
            )
        else:
            # Only after createIndexes returned: a listed index may still be
            # building, and its unique constraint is enforced only on commit.
            if await alert_idempotency_index_ready(db):
                logger.info("Alert idempotency index verified; alerts enabled")
                on_ready()
                return
        await sleep(delay)
        delay = min(delay * 2, max_delay)


def _now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _retry_delay(attempts: int) -> timedelta:
    """Compute exponential backoff from the persisted delivery attempt count."""
    return RETRY_BASE_DELAY * (2 ** max(0, attempts - 1))


def _claimable(now: datetime) -> list:
    """States a scan may claim. ``sent``/``skipped``/live ``sending`` are excluded."""
    iso = now.isoformat()
    return [
        {"status": {"$exists": False}},
        {
            "status": "failed",
            "nextAttemptAt": {"$lte": iso},
            "attempts": {"$lt": MAX_ATTEMPTS},
        },
        {
            "status": "sending",
            "claimedAt": {"$lte": (now - CLAIM_LEASE).isoformat()},
            "attempts": {"$lt": MAX_ATTEMPTS},
        },
    ]


def _claim_update(token: str) -> dict:
    """Atomically mark claim ownership and count an attempt before delivery."""
    # attempts is incremented here, atomically with the claim, so each provider
    # attempt is counted durably before it happens. ``claimedAt`` is stamped at
    # claim time (not scan start) so a long scan cannot shorten the lease.
    return {
        "$set": {
            "status": "sending",
            "claimToken": token,
            "claimedAt": _now().isoformat(),
        },
        "$inc": {"attempts": 1},
    }


async def _deliver(
    db, claim: dict, recipient_email: str, contract: dict, days: int, counts: dict
) -> None:
    """Make exactly one provider attempt for a claimed record and persist the outcome."""
    token = claim["claimToken"]
    try:
        result = await send_expiration_alert(
            to_email=recipient_email,
            contract_id=contract["id"],
            contract_title=contract.get("title", "Untitled Contract"),
            counterparty=contract.get("counterparty", "Not specified"),
            expiry_date=contract.get("expiryDate", "Unknown"),
            days_remaining=days,
        )
    except Exception:
        logger.exception("Expiration alert provider call raised")
        result = {"status": "error"}
    provider_status = (result or {}).get("status")
    owned = {"_id": claim["_id"], "claimToken": token}
    if provider_status == "success":
        await db.expiration_alerts.update_one(
            owned,
            {
                "$set": {
                    "status": "sent",
                    "emailSent": True,
                    "emailSentAt": _now().isoformat(),
                    "providerMessageId": (result or {}).get("email_id"),
                },
                "$unset": {"nextAttemptAt": ""},
            },
        )
        counts["sent"] += 1
    elif provider_status == "logged":
        await db.expiration_alerts.update_one(
            owned,
            {
                "$set": {
                    "status": "skipped",
                    "emailSent": False,
                    "failureReason": "provider_not_configured",
                }
            },
        )
        counts["skipped"] += 1
    else:
        attempts = int(claim.get("attempts", 1))
        update = {
            "status": "failed",
            "emailSent": False,
            "failureReason": "provider_delivery_failed",
        }
        if attempts < MAX_ATTEMPTS:
            update["nextAttemptAt"] = (_now() + _retry_delay(attempts)).isoformat()
        else:
            update["failureReason"] = "max_attempts_exhausted"
        await db.expiration_alerts.update_one(owned, {"$set": update})
        counts["failed"] += 1


async def _recipients(db, org_id: str) -> list:
    """Load active administrators and managers belonging to one organization."""
    return await db.users.find(
        {
            "organizationId": org_id,
            "role": {"$in": ["admin", "manager"]},
            "isActive": {"$ne": False},
        },
        {"_id": 0, "id": 1, "email": 1},
    ).to_list(100)


async def retry_failed_alerts(
    db, organization_id: Optional[str] = None, counts: Optional[dict] = None
) -> dict:
    """Retry due ``failed`` records independently of the expiry-date selection."""
    counts = (
        counts
        if counts is not None
        else {"queued": 0, "sent": 0, "failed": 0, "skipped": 0}
    )
    now = _now()
    query = {
        "status": "failed",
        "nextAttemptAt": {"$lte": now.isoformat()},
        "attempts": {"$lt": MAX_ATTEMPTS},
    }
    if organization_id:
        query["organizationId"] = organization_id
    due = await db.expiration_alerts.find(
        query,
        {
            "_id": 1,
            "organizationId": 1,
            "contractId": 1,
            "userId": 1,
            "daysBeforeExpiry": 1,
        },
    ).to_list(1000)
    for record in due:
        org_id = record.get("organizationId")
        contract = await db.contracts.find_one(
            {
                "id": record.get("contractId"),
                "organizationId": org_id,
                "status": {"$ne": "expired"},
            },
            {"_id": 0, "id": 1, "title": 1, "counterparty": 1, "expiryDate": 1},
        )
        recipient = await db.users.find_one(
            {
                "id": record.get("userId"),
                "organizationId": org_id,
                "role": {"$in": ["admin", "manager"]},
                "isActive": {"$ne": False},
            },
            {"_id": 0, "id": 1, "email": 1},
        )
        if not contract or not recipient:
            await db.expiration_alerts.update_one(
                {"_id": record["_id"], "status": "failed"},
                {
                    "$set": {
                        "status": "skipped",
                        "failureReason": "contract_or_recipient_no_longer_eligible",
                    }
                },
            )
            counts["skipped"] += 1
            continue
        token = uuid.uuid4().hex
        claim = await db.expiration_alerts.find_one_and_update(
            {"_id": record["_id"], "$or": _claimable(now)},
            _claim_update(token),
            return_document=ReturnDocument.AFTER,
        )
        if not claim or claim.get("claimToken") != token:
            continue
        counts["queued"] += 1
        await _deliver(
            db,
            claim,
            recipient["email"],
            contract,
            int(record.get("daysBeforeExpiry", 0)),
            counts,
        )
    return counts


async def scan_expiration_alerts(
    db, organization_id: Optional[str] = None, actor_id: str = "scheduler"
) -> dict:
    """Scan organizations and atomically claim/send each alert once, then retry due failures."""
    if organization_id:
        org_ids = [organization_id]
    else:
        org_ids = await db.contracts.distinct(
            "organizationId", {"expiryDate": {"$type": "string"}}
        )

    counts = {"queued": 0, "sent": 0, "failed": 0, "skipped": 0}
    now = _now()
    for org_id in org_ids:
        settings = (
            await db.alert_settings.find_one({"organizationId": org_id}, {"_id": 0})
            or {}
        )
        if not settings.get("emailEnabled", True):
            continue
        alert_days = settings.get("alertDays") or list(DEFAULT_ALERT_DAYS)
        recipients = await _recipients(db, org_id)
        if not recipients:
            continue
        for days in alert_days:
            try:
                days = int(days)
            except (TypeError, ValueError):
                continue
            # Exact-date selection keeps the intended cadence: a contract
            # matches one ``days`` window per day, never several at once.
            target = (now + timedelta(days=days)).strftime("%Y-%m-%d")
            contracts = await db.contracts.find(
                {
                    "organizationId": org_id,
                    "expiryDate": target,
                    "status": {"$ne": "expired"},
                },
                {"_id": 0, "id": 1, "title": 1, "counterparty": 1, "expiryDate": 1},
            ).to_list(1000)
            for contract in contracts:
                for recipient in recipients:
                    key = (
                        f"{org_id}:{contract['id']}:expiration:{days}:{recipient['id']}"
                    )
                    token = uuid.uuid4().hex
                    try:
                        claim = await db.expiration_alerts.find_one_and_update(
                            {"idempotencyKey": key, "$or": _claimable(now)},
                            {
                                **_claim_update(token),
                                "$setOnInsert": {
                                    "id": str(uuid.uuid4()),
                                    "idempotencyKey": key,
                                    "organizationId": org_id,
                                    "contractId": contract["id"],
                                    "userId": recipient["id"],
                                    "alertType": "expiration",
                                    "daysBeforeExpiry": days,
                                    "emailSent": False,
                                    "createdAt": now.isoformat(),
                                },
                            },
                            upsert=True,
                            return_document=ReturnDocument.AFTER,
                        )
                    except DuplicateKeyError:
                        # Record exists but is sent/skipped/being sent elsewhere.
                        continue
                    if not claim or claim.get("claimToken") != token:
                        continue
                    counts["queued"] += 1
                    await _deliver(
                        db, claim, recipient["email"], contract, days, counts
                    )
        await retry_failed_alerts(db, org_id, counts)
    return counts
