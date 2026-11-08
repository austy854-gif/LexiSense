from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import logging

from models.alerts import AlertSettings
from utils.auth import get_current_user
from services.alert_service import scan_expiration_alerts
from utils.policy import require_capability
from routes.billing import assert_org_writable

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["Alerts"])

db = None

def init_db(database):
    global db
    db = database


@router.get("/settings")
async def get_alert_settings(current_user: dict = Depends(get_current_user)):
    """Get alert settings for the organization."""
    settings = await db.alert_settings.find_one(
        {"organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    
    if not settings:
        # Return default settings
        return {
            "alertDays": [30, 14, 7, 1],
            "emailEnabled": True
        }
    
    return settings


@router.put("/settings")
async def update_alert_settings(
    alertDays: List[int] = [30, 14, 7, 1],
    emailEnabled: bool = True,
    current_user: dict = Depends(get_current_user)
):
    """Update alert settings for the organization."""
    require_capability(current_user, "alert_settings")
    await assert_org_writable(current_user["organizationId"])
    
    settings = AlertSettings(
        organizationId=current_user["organizationId"],
        alertDays=sorted(alertDays, reverse=True),
        emailEnabled=emailEnabled
    )
    
    await db.alert_settings.update_one(
        {"organizationId": current_user["organizationId"]},
        {"$set": settings.model_dump()},
        upsert=True
    )
    
    return {"message": "Alert settings updated", "settings": settings.model_dump()}


@router.get("/expiring")
async def get_expiring_contracts(
    days: int = 30,
    current_user: dict = Depends(get_current_user)
):
    """Get contracts expiring within specified days."""
    org_id = current_user["organizationId"]
    today = datetime.now(timezone.utc)
    future_date = (today + timedelta(days=days)).isoformat()
    today_str = today.isoformat()
    
    expiring = await db.contracts.find(
        {
            "organizationId": org_id,
            "expiryDate": {"$ne": None, "$gte": today_str[:10], "$lte": future_date[:10]},
            "status": {"$ne": "expired"}
        },
        {"_id": 0, "originalText": 0}
    ).sort("expiryDate", 1).to_list(100)
    
    # Calculate days remaining for each contract
    for contract in expiring:
        if contract.get("expiryDate"):
            try:
                expiry = datetime.fromisoformat(contract["expiryDate"].replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                days_remaining = (expiry - today).days
                contract["daysRemaining"] = max(0, days_remaining)
            except:
                contract["daysRemaining"] = None
    
    return {"contracts": expiring, "total": len(expiring)}


@router.post("/check-and-send")
async def check_and_send_alerts(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Queue the same idempotent scan used by the daily worker.

    Delivery talks to the email provider once per (contract x window x
    recipient), so it runs as a background task instead of on the request path;
    the endpoint answers 202 immediately and results appear in alert history.
    """
    require_capability(current_user, "alert_trigger")
    await assert_org_writable(current_user["organizationId"])
    if not getattr(request.app.state, "alerts_ready", True):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Alerts are temporarily unavailable. Please try again shortly.",
        )
    background_tasks.add_task(_run_manual_scan, current_user["organizationId"], current_user["sub"])
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"message": "Alert check started", "status": "accepted"},
    )


async def _run_manual_scan(organization_id: str, actor_id: str) -> None:
    """Run an authorized manual scan without leaking background failures to the request."""
    try:
        result = await scan_expiration_alerts(db, organization_id, actor_id)
        logger.info("Manual expiration alert scan complete", extra=result)
    except Exception:
        logger.exception("Manual expiration alert scan failed")


@router.get("/history")
async def get_alert_history(
    limit: int = 50,
    current_user: dict = Depends(get_current_user)
):
    """Get history of sent alerts."""
    contracts = await db.contracts.find(
        {"organizationId": current_user["organizationId"]},
        {"_id": 0, "id": 1, "title": 1}
    ).to_list(1000)
    contract_ids = [c["id"] for c in contracts]
    contract_titles = {c["id"]: c["title"] for c in contracts}
    alerts = await db.expiration_alerts.find(
        {
            "contractId": {"$in": contract_ids},
            # Legacy ExpirationAlert rows predate organizationId; the contractId
            # predicate already scopes them to this organization.
            "$or": [
                {"organizationId": current_user["organizationId"]},
                {"organizationId": {"$exists": False}},
            ],
        },
        {"_id": 0}
    ).sort("createdAt", -1).limit(limit).to_list(limit)
    
    # Add contract titles
    for alert in alerts:
        alert["contractTitle"] = contract_titles.get(alert["contractId"], "Unknown")
    
    return {"alerts": alerts}
