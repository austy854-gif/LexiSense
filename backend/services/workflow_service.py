"""Contract workflow state machine.

Single source of truth for the allowed status transitions, the roles that may
drive them, and the side effects (workflow history + audit log) that every
status change must record.

Both the dedicated workflow endpoints (``routes/workflow.py``) and the generic
contract mutation endpoints (``routes/contracts.py`` -- ``PATCH`` and version
restore) route through :func:`apply_status_transition`, so a contract's status
can never change without passing the transition check, the role gate and the
audit trail.

The defect this closes
----------------------
``PATCH /contracts/{id}`` and ``POST /contracts/{id}/restore/{version}`` both
wrote ``status`` directly, so any non-viewer could move a contract straight from
``draft`` to ``approved`` (or roll an ``approved`` contract back to ``draft``)
without the state machine ever seeing it -- no transition check, no role gate,
no ``workflowHistory`` entry and no audit log. Routing both endpoints through
this module makes the state machine the only way a status can change.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status

from services.audit_service import log_action

#: Allowed status transitions. ``expired`` is terminal.
WORKFLOW_TRANSITIONS = {
    "draft": ["review"],
    "review": ["approved", "draft"],
    "approved": ["active", "review"],
    "active": ["expired"],
    "expired": [],
}

WORKFLOW_LABELS = {
    "draft": "Draft",
    "review": "In Review",
    "approved": "Approved",
    "active": "Active",
    "expired": "Expired",
}

#: Every status the application understands.
VALID_STATUSES = tuple(WORKFLOW_LABELS.keys())

#: Roles allowed to drive a status transition at all. ``viewer`` is read-only.
TRANSITION_ROLES = ("admin", "manager", "user")

#: Roles allowed to perform the privileged transitions (approve / activate / reject).
PRIVILEGED_TRANSITION_ROLES = ("admin", "manager")


def validate_transition(current_status: str, new_status: str) -> None:
    """Raise ``400`` unless the state machine allows ``current -> new``.

    A no-op transition (``current == new``) is always allowed -- it is not a
    transition at all.
    """
    if new_status == current_status:
        return
    allowed = WORKFLOW_TRANSITIONS.get(current_status, [])
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition from '{current_status}' to '{new_status}'",
        )


def build_history_entry(
    current_status: str,
    new_status: str,
    current_user: dict,
    *,
    action: str,
    comment: Optional[str] = None,
) -> dict:
    """Build the ``workflowHistory`` entry for a status change."""
    return {
        "fromStatus": current_status,
        "toStatus": new_status,
        "action": action,
        "userId": current_user["sub"],
        "userEmail": current_user.get("email"),
        "comment": comment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def status_update_fields(
    current_status: str,
    new_status: str,
    current_user: dict,
    *,
    action: str,
    comment: Optional[str] = None,
) -> dict:
    """Return the ``$set`` / ``$push`` fragment for a status change.

    Callers merge this into their own update so the status change, the
    ``workflowHistory`` entry and any other field edits land in a single write.
    """
    now = datetime.now(timezone.utc).isoformat()
    set_fields = {"status": new_status, "updatedAt": now}
    if new_status == "approved":
        set_fields["approvedBy"] = current_user["sub"]
        set_fields["approvedAt"] = now
    return {
        "$set": set_fields,
        "$push": {
            "workflowHistory": build_history_entry(
                current_status,
                new_status,
                current_user,
                action=action,
                comment=comment,
            )
        },
    }


async def record_transition_audit(
    contract: dict,
    current_status: str,
    new_status: str,
    current_user: dict,
    *,
    action: str,
    comment: Optional[str] = None,
) -> None:
    """Write the audit-log entry for a status change."""
    await log_action(
        organization_id=current_user["organizationId"],
        user_id=current_user["sub"],
        user_email=current_user.get("email"),
        action=f"contract_{action}",
        resource_type="contract",
        resource_id=contract["id"],
        resource_title=contract.get("title"),
        details={"from": current_status, "to": new_status, "comment": comment},
    )


async def apply_status_transition(
    db,
    contract: dict,
    new_status: str,
    current_user: dict,
    *,
    action: str,
    comment: Optional[str] = None,
) -> dict:
    """Validate and apply a status transition, recording history + audit.

    The caller is responsible for the *write* authorisation gate (viewer /
    ownership); this function enforces the *state machine* gate and records the
    side effects. Returns the appended workflow-history entry.
    """
    current_status = contract.get("status", "draft")
    validate_transition(current_status, new_status)

    update = status_update_fields(
        current_status, new_status, current_user, action=action, comment=comment
    )
    await db.contracts.update_one({"id": contract["id"]}, update)
    await record_transition_audit(
        contract, current_status, new_status, current_user, action=action, comment=comment
    )
    return update["$push"]["workflowHistory"]
