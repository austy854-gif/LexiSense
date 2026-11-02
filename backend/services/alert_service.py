"""Organization-scoped expiration/renewal alert scan used by API and scheduler."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from models.alerts import ExpirationAlert
from services.email_service import send_expiration_alert

logger = logging.getLogger(__name__)
DEFAULT_ALERT_DAYS = (30, 14, 7, 1)
MAX_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def scan_expiration_alerts(db, organization_id: Optional[str] = None, actor_id: str = "scheduler") -> dict:
    """Scan organizations and atomically claim/send each alert once.

    The idempotency key includes organization, contract, recipient, alert type,
    and expiry window. A unique index plus an atomic claim prevents duplicate
    sends across worker restarts and multiple scheduler instances.
    """
    if organization_id:
        org_ids = [organization_id]
    else:
        org_ids = await db.contracts.distinct("organizationId", {"expiryDate": {"$type": "string"}})

    counts = {"queued": 0, "sent": 0, "failed": 0, "skipped": 0}
    now = _now()
    for org_id in org_ids:
        settings = await db.alert_settings.find_one({"organizationId": org_id}, {"_id": 0}) or {}
        if not settings.get("emailEnabled", True):
            continue
        alert_days = settings.get("alertDays") or list(DEFAULT_ALERT_DAYS)
        recipients = await db.users.find(
            {"organizationId": org_id, "role": {"$in": ["admin", "manager"]}, "isActive": {"$ne": False}},
            {"_id": 0, "id": 1, "email": 1},
        ).to_list(100)
        if not recipients:
            continue
        for days in alert_days:
            try:
                days = int(days)
            except (TypeError, ValueError):
                continue
            target = (now + timedelta(days=days)).strftime("%Y-%m-%d")
            contracts = await db.contracts.find(
                {"organizationId": org_id, "expiryDate": target, "status": {"$ne": "expired"}},
                {"_id": 0, "id": 1, "title": 1, "counterparty": 1, "expiryDate": 1},
            ).to_list(1000)
            for contract in contracts:
                for recipient in recipients:
                    key = f"{org_id}:{contract['id']}:expiration:{days}:{recipient['id']}"
                    claim = None
                    try:
                        claim = await db.expiration_alerts.find_one_and_update(
                            {
                                "idempotencyKey": key,
                                "$or": [
                                    {"status": {"$exists": False}},
                                    {"status": "queued"},
                                    {"status": "failed", "nextAttemptAt": {"$lte": now.isoformat()}, "attempts": {"$lt": MAX_ATTEMPTS}},
                                ],
                            },
                            {"$set": {"status": "queued", "queuedAt": now.isoformat(), "organizationId": org_id, "userId": recipient["id"]},
                             "$setOnInsert": {"id": str(__import__('uuid').uuid4()), "idempotencyKey": key, "contractId": contract["id"], "alertType": "expiration", "daysBeforeExpiry": days, "emailSent": False, "createdAt": now.isoformat(), "attempts": 0}},
                            upsert=True,
                            return_document=ReturnDocument.AFTER,
                        )
                    except DuplicateKeyError:
                        continue
                    # Existing sent/skipped records are not claimable.
                    if not claim or claim.get("status") != "queued":
                        continue
                    counts["queued"] += 1
                    await db.expiration_alerts.update_one({"_id": claim["_id"]}, {"$inc": {"attempts": 1}})
                    result = None
                    for attempt in range(MAX_ATTEMPTS):
                        try:
                            result = await send_expiration_alert(
                                to_email=recipient["email"], contract_id=contract["id"],
                                contract_title=contract.get("title", "Untitled Contract"),
                                counterparty=contract.get("counterparty", "Not specified"),
                                expiry_date=contract.get("expiryDate", "Unknown"), days_remaining=days,
                            )
                        except Exception:
                            result = {"status": "error"}
                        if result and result.get("status") == "success":
                            break
                        if result and result.get("status") == "logged":
                            break
                        if attempt < MAX_ATTEMPTS - 1:
                            await asyncio.sleep(2 ** attempt)
                    provider_status = (result or {}).get("status")
                    if provider_status == "success":
                        await db.expiration_alerts.update_one({"_id": claim["_id"]}, {"$set": {"status": "sent", "emailSent": True, "emailSentAt": _now().isoformat(), "providerMessageId": (result or {}).get("email_id")}})
                        counts["sent"] += 1
                    elif provider_status == "logged":
                        await db.expiration_alerts.update_one({"_id": claim["_id"]}, {"$set": {"status": "skipped", "emailSent": False, "failureReason": "provider_not_configured"}})
                        counts["skipped"] += 1
                    else:
                        await db.expiration_alerts.update_one({"_id": claim["_id"]}, {"$set": {"status": "failed", "emailSent": False, "nextAttemptAt": (_now() + timedelta(minutes=5)).isoformat(), "failureReason": "provider_delivery_failed"}})
                        counts["failed"] += 1
    return counts
