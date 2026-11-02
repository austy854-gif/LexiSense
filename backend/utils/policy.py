"""Authoritative server-side policy for active contract workspace writes.

Every mutating route must use these helpers after authenticating and, where a
resource is involved, after loading it with an organization-scoped query.
The UI mirrors these capabilities for affordances only; this module is the
security boundary.
"""
from __future__ import annotations

from typing import Optional
from fastapi import HTTPException, status

from utils.rbac import ROLE_ADMIN, ROLE_MANAGER, ROLE_USER, ROLE_VIEWER, normalize_role

# The product role matrix is intentionally explicit. Users may upload and
# submit their own work, but managers/admins own all other contract mutations.
CAPABILITIES = {
    ROLE_ADMIN: frozenset({
        "upload", "delete", "restore", "version", "submit_for_review",
        "approve", "reject", "activate", "template_manage", "invite",
        "audit_view", "alert_settings", "alert_trigger", "contract_edit",
        "tag_manage",
    }),
    ROLE_MANAGER: frozenset({
        "upload", "delete", "restore", "version", "submit_for_review",
        "approve", "reject", "activate", "audit_view", "alert_trigger",
        "contract_edit", "tag_manage",
    }),
    ROLE_USER: frozenset({"upload", "submit_for_review"}),
    ROLE_VIEWER: frozenset(),
}

# Capabilities a role holds only for contracts it uploaded itself. A ``user``
# may edit and restore their own uploads (pre-existing contract behaviour,
# covered by the PATCH/restore RBAC suites); viewers never write.
OWNER_CAPABILITIES = {
    ROLE_USER: frozenset({"contract_edit", "restore"}),
}

_OWNER_DENIAL = {
    "contract_edit": "Only admins, managers, or the uploader can modify this contract",
    "restore": "Only admins, managers, or the uploader can restore this contract",
}


def can(role: object, capability: str) -> bool:
    return capability in CAPABILITIES.get(normalize_role(role), frozenset())


def deny(capability: str) -> HTTPException:
    labels = {
        "template_manage": "Only admins can manage templates.",
        "alert_settings": "Only admins can update alert settings.",
        "audit_view": "Only admins and managers can view audit logs.",
    }
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=labels.get(capability, f"Your role cannot perform '{capability}'."),
    )


def require_capability(user: dict, capability: str) -> None:
    if not can(user.get("role"), capability):
        raise deny(capability)


def require_owner_or_capability(user: dict, resource: dict, capability: str) -> None:
    """Require a capability and organization ownership for a loaded resource."""
    if resource.get("organizationId") != user.get("organizationId"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    require_capability(user, capability)


def require_contract_write(user: dict, contract: dict, capability: str) -> None:
    """Authorize a write on an org-scoped, already-loaded contract.

    Admins/managers hold the capability outright; a ``user`` holds it only for
    contracts they uploaded; viewers are always read-only.
    """
    if contract.get("organizationId") != user.get("organizationId"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
    role = normalize_role(user.get("role"))
    if can(role, capability):
        return
    if capability in OWNER_CAPABILITIES.get(role, frozenset()):
        if contract.get("uploadedBy") == user.get("sub"):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_OWNER_DENIAL.get(capability, f"Your role cannot perform '{capability}'."),
        )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Viewers cannot modify contracts")


def require_uploader_for_user(user: dict, resource: dict) -> None:
    """Users may submit only contracts they uploaded themselves."""
    if normalize_role(user.get("role")) == ROLE_USER and resource.get("uploadedBy") != user.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Users may only submit contracts they uploaded.",
        )


def capability_map(role: object, *, read_only: bool = False, uploads_blocked: bool = False) -> dict[str, bool]:
    """Serialize frontend capabilities from the same server-side matrix.

    ``can*Own`` flags mean "allowed only on contracts this user uploaded".
    """
    normalized = normalize_role(role)
    owner = OWNER_CAPABILITIES.get(normalized, frozenset())
    return {
        "canUpload": can(normalized, "upload") and not read_only and not uploads_blocked,
        "canDelete": can(normalized, "delete") and not read_only,
        "canRestore": can(normalized, "restore") and not read_only,
        "canSubmitForReview": can(normalized, "submit_for_review") and not read_only,
        "canApprove": can(normalized, "approve") and not read_only,
        "canReject": can(normalized, "reject") and not read_only,
        "canActivate": can(normalized, "activate") and not read_only,
        "canInvite": can(normalized, "invite") and not read_only,
        "canManageTemplates": can(normalized, "template_manage") and not read_only,
        "canViewAudit": can(normalized, "audit_view"),
        "canUpdateAlertSettings": can(normalized, "alert_settings") and not read_only,
        "canTriggerAlerts": can(normalized, "alert_trigger") and not read_only,
        "canEditContract": can(normalized, "contract_edit") and not read_only,
        "canEditOwnContract": ("contract_edit" in owner or can(normalized, "contract_edit")) and not read_only,
        "canRestoreOwn": ("restore" in owner or can(normalized, "restore")) and not read_only,
    }
