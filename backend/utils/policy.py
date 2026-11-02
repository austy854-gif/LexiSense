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


def require_uploader_for_user(user: dict, resource: dict) -> None:
    """Users may submit only contracts they uploaded themselves."""
    if normalize_role(user.get("role")) == ROLE_USER and resource.get("uploadedBy") != user.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Users may only submit contracts they uploaded.",
        )


def capability_map(role: object, *, read_only: bool = False) -> dict[str, bool]:
    """Serialize frontend capabilities from the same server-side matrix."""
    normalized = normalize_role(role)
    return {
        "canUpload": can(normalized, "upload") and not read_only,
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
    }
