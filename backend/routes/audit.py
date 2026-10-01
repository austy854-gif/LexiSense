from fastapi import APIRouter, Depends, Query
from typing import List, Optional
import logging

from models.audit import AuditLogResponse
from utils.auth import get_current_user, require_role
from utils.rbac import AUDIT_READER_ROLES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["Audit"])

#: Audit data is organisation-sensitive: admin/manager only, from the registry.
_require_audit_reader = require_role(*AUDIT_READER_ROLES)

db = None

def init_db(database):
    global db
    db = database


@router.get("", response_model=List[AuditLogResponse])
async def list_audit_logs(
    resource_type: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    current_user: dict = Depends(_require_audit_reader),
):
    """List audit logs for the organization. Admin/manager only.

    Previously an unauthorised caller received ``200 []``, which is
    indistinguishable from "this organisation has no audit history" -- the
    endpoint now returns a proper 403 from the shared role registry.
    """
    query = {"organizationId": current_user["organizationId"]}
    if resource_type:
        query["resourceType"] = resource_type
    if action:
        query["action"] = action

    logs = (
        await db.audit_logs.find(query, {"_id": 0})
        .sort("createdAt", -1)
        .limit(limit)
        .to_list(limit)
    )

    return [
        AuditLogResponse(
            id=l["id"],
            userId=l["userId"],
            userEmail=l.get("userEmail"),
            action=l["action"],
            resourceType=l["resourceType"],
            resourceId=l.get("resourceId"),
            resourceTitle=l.get("resourceTitle"),
            details=l.get("details"),
            createdAt=l["createdAt"],
        )
        for l in logs
    ]
