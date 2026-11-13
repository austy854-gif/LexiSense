"""Authoritative server-side role policy.

``CAPABILITIES`` is the role matrix and ``ROUTE_POLICIES`` names the capability
(and billing feature) each API endpoint needs. ``utils.auth.get_current_user``
enforces the route row on every authenticated request, denying any endpoint
without one. Handlers still use the helpers below for resource-level checks
(ownership, organization scoping) after loading the resource with an
organization-scoped query. The UI mirrors these capabilities for affordances
only; this module is the security boundary. See docs/entitlements-and-roles.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException, status

from utils.billing_access import (
    FEATURE_AI,
    FEATURE_INVITE,
    FEATURE_UPLOAD,
    FEATURE_WRITE,
)
from utils.rbac import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_USER,
    ROLE_VIEWER,
    ROLES,
    normalize_role,
)

logger = logging.getLogger(__name__)

# The product role matrix is intentionally explicit. Users may upload and
# submit their own work, but managers/admins own all other contract mutations.
#
# Capabilities every authenticated member holds, whatever their role: reading
# organisation data, acting on their own session/notifications, and asking the
# AI about a contract they can already read (``ai_chat``; still subject to the
# organisation's AI entitlement and the chat rate limits).
MEMBER_CAPABILITIES = frozenset({"read", "self", "ai_chat"})

# Agentic (feature-flagged) capabilities. ``ai_task`` queues single-contract
# risk/obligation work; ``ai_bulk`` queues org-wide runs.
_AGENTIC_MANAGER = frozenset(
    {
        "agent_manage",
        "playbook_manage",
        "redline_run",
        "redline_apply",
        "intake_process",
        "ai_task",
        "ai_bulk",
        "obligation_update",
    }
)

CAPABILITIES = {
    ROLE_ADMIN: frozenset(
        {
            "upload",
            "delete",
            "restore",
            "version",
            "submit_for_review",
            "approve",
            "reject",
            "activate",
            "template_manage",
            "invite",
            "team_manage",
            "billing_manage",
            "audit_view",
            "alert_settings",
            "alert_trigger",
            "contract_edit",
            "tag_manage",
            "agent_delete",
            "playbook_delete",
        }
    )
    | _AGENTIC_MANAGER
    | MEMBER_CAPABILITIES,
    ROLE_MANAGER: frozenset(
        {
            "upload",
            "delete",
            "restore",
            "version",
            "submit_for_review",
            "approve",
            "reject",
            "activate",
            "audit_view",
            "alert_trigger",
            "contract_edit",
            "tag_manage",
        }
    )
    | _AGENTIC_MANAGER
    | MEMBER_CAPABILITIES,
    ROLE_USER: frozenset(
        {"upload", "submit_for_review", "redline_run", "ai_task", "obligation_update"}
    )
    | MEMBER_CAPABILITIES,
    ROLE_VIEWER: MEMBER_CAPABILITIES,
}

#: Capabilities that mutate a contract; a viewer denied one gets the
#: established "Viewers cannot modify contracts" message.
CONTRACT_WRITE_CAPABILITIES = frozenset(
    {
        "upload",
        "delete",
        "restore",
        "version",
        "submit_for_review",
        "approve",
        "reject",
        "activate",
        "contract_edit",
        "tag_manage",
    }
)

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


_DENIAL_LABELS = {
    "template_manage": "Only admins can manage templates.",
    "alert_settings": "Only admins can update alert settings.",
    "audit_view": "Only admins and managers can view audit logs.",
}


def can(role: object, capability: str) -> bool:
    """Return whether the normalized role grants the requested capability."""
    return capability in CAPABILITIES.get(normalize_role(role), frozenset())


def deny(capability: str) -> HTTPException:
    """Build a capability-specific forbidden response without exposing resource data."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=_DENIAL_LABELS.get(
            capability, f"Your role cannot perform '{capability}'."
        ),
    )


def require_capability(user: dict, capability: str) -> None:
    """Reject users whose role does not grant the requested capability."""
    if not can(user.get("role"), capability):
        raise deny(capability)


def require_org_capability(user: dict, resource: dict, capability: str) -> None:
    """Require a capability on a resource that belongs to the caller's organization."""
    if resource.get("organizationId") != user.get("organizationId"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    require_capability(user, capability)


def require_contract_write(user: dict, contract: dict, capability: str) -> None:
    """Authorize a write on an org-scoped, already-loaded contract.

    Admins/managers hold the capability outright; a ``user`` holds it only for
    contracts they uploaded; viewers are always read-only.
    """
    if contract.get("organizationId") != user.get("organizationId"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found"
        )
    role = normalize_role(user.get("role"))
    if can(role, capability):
        return
    if capability in OWNER_CAPABILITIES.get(role, frozenset()):
        if contract.get("uploadedBy") == user.get("sub"):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_OWNER_DENIAL.get(
                capability, f"Your role cannot perform '{capability}'."
            ),
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Viewers cannot modify contracts"
    )


def require_uploader_for_user(user: dict, resource: dict) -> None:
    """Users may submit only contracts they uploaded themselves."""
    if normalize_role(user.get("role")) == ROLE_USER and resource.get(
        "uploadedBy"
    ) != user.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Users may only submit contracts they uploaded.",
        )


def capability_map(
    role: object, *, read_only: bool = False, uploads_blocked: bool = False
) -> dict[str, bool]:
    """Serialize frontend capabilities from the same server-side matrix.

    ``can*Own`` flags mean "allowed only on contracts this user uploaded".
    """
    normalized = normalize_role(role)
    owner = OWNER_CAPABILITIES.get(normalized, frozenset())
    return {
        "canUpload": can(normalized, "upload")
        and not read_only
        and not uploads_blocked,
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
        "canEditOwnContract": (
            "contract_edit" in owner or can(normalized, "contract_edit")
        )
        and not read_only,
        "canRestoreOwn": ("restore" in owner or can(normalized, "restore"))
        and not read_only,
    }


# ---------------------------------------------------------------------------
# Route policy: one row per API endpoint (deny by default)
# ---------------------------------------------------------------------------
# Entitlement features come from ``utils.billing_access.ENTITLEMENT_GRID``. The
# route handler enforces the feature against the organisation's billing state;
# the table records which one applies so the grid tests can check every route.


@dataclass(frozen=True)
class RoutePolicy:
    """Who may call one endpoint.

    ``capability`` is the role capability required (``None`` for a public,
    unauthenticated endpoint). ``any_of`` accepts any one of several
    capabilities (the workflow endpoint, whose action is a path parameter; the
    handler then checks the exact one). ``owner_scoped`` lets a role that holds
    the capability only for its own uploads through; the handler then checks
    ownership. ``feature`` is the entitlement feature the handler enforces.
    """

    capability: Optional[str] = None
    any_of: Tuple[str, ...] = ()
    owner_scoped: bool = False
    feature: Optional[str] = None

    @property
    def public(self) -> bool:
        return self.capability is None and not self.any_of


PUBLIC = RoutePolicy()
READ = RoutePolicy("read")
SELF = RoutePolicy("self")


def _p(capability: str, feature: Optional[str] = None, **kw) -> RoutePolicy:
    return RoutePolicy(capability, feature=feature, **kw)


#: Endpoint (``module.function``) -> policy. Every route the API can mount
#: (including feature-flagged agentic and approval-email routes) must have a
#: row; an authenticated route without one is refused with 403.
ROUTE_POLICIES: Dict[str, RoutePolicy] = {
    # --- server / health -----------------------------------------------------
    "server.root": PUBLIC,
    "server.health_check": PUBLIC,
    "server.readiness_check": PUBLIC,
    # --- auth ----------------------------------------------------------------
    "routes.auth.register": PUBLIC,
    "routes.auth.login": PUBLIC,
    "routes.auth.forgot_password": PUBLIC,
    "routes.auth.reset_password": PUBLIC,
    "routes.auth.get_current_user_info": SELF,
    "routes.auth.logout": SELF,
    # --- contracts -----------------------------------------------------------
    "routes.contracts.list_contracts": READ,
    "routes.contracts.get_contract": READ,
    "routes.contracts.get_contract_text": READ,
    "routes.contracts.download_contract_file": READ,
    "routes.contracts.get_contract_versions": READ,
    "routes.contracts.get_contract_version": READ,
    "routes.contracts.upload_contract": _p("upload", FEATURE_UPLOAD),
    "routes.contracts.bulk_upload_contracts": _p("upload", FEATURE_UPLOAD),
    "routes.contracts.chat_with_contract": _p("ai_chat", FEATURE_AI),
    "routes.contracts.update_contract": _p(
        "contract_edit", FEATURE_WRITE, owner_scoped=True
    ),
    "routes.contracts.restore_contract_version": _p(
        "restore", FEATURE_WRITE, owner_scoped=True
    ),
    "routes.contracts.delete_contract": _p("delete", FEATURE_WRITE),
    # --- workflow ------------------------------------------------------------
    "routes.workflow.get_workflow_states": READ,
    "routes.workflow.get_workflow_history": READ,
    "routes.workflow.workflow_action": RoutePolicy(
        any_of=("submit_for_review", "approve", "reject", "activate"),
        feature=FEATURE_WRITE,
    ),
    # --- tags ----------------------------------------------------------------
    "routes.tags.list_organization_tags": READ,
    "routes.tags.add_tags": _p("contract_edit", FEATURE_WRITE, owner_scoped=True),
    "routes.tags.remove_tag": _p("contract_edit", FEATURE_WRITE, owner_scoped=True),
    "routes.tags.update_category": _p(
        "contract_edit", FEATURE_WRITE, owner_scoped=True
    ),
    # --- team ----------------------------------------------------------------
    "routes.team.list_team_members": READ,
    "routes.team.invite_member": _p("invite", FEATURE_INVITE),
    "routes.team.list_invitations": _p("team_manage"),
    "routes.team.cancel_invitation": _p("team_manage"),
    "routes.team.update_member_role": _p("team_manage"),
    "routes.team.remove_member": _p("team_manage"),
    "routes.team.preview_invitation": PUBLIC,
    "routes.team.accept_invitation": PUBLIC,
    # --- dashboard / analytics / export / audit -------------------------------
    "routes.dashboard.get_dashboard_stats": READ,
    "routes.dashboard.get_recent_activity": READ,
    "routes.analytics.get_analytics_overview": READ,
    "routes.analytics.compare_contracts": READ,
    "routes.export.export_contract_pdf": READ,
    "routes.export.export_analytics_pdf": READ,
    "routes.audit.list_audit_logs": _p("audit_view"),
    # --- alerts --------------------------------------------------------------
    "routes.alerts.get_alert_settings": READ,
    "routes.alerts.get_expiring_contracts": READ,
    "routes.alerts.get_alert_history": READ,
    "routes.alerts.update_alert_settings": _p("alert_settings", FEATURE_WRITE),
    "routes.alerts.check_and_send_alerts": _p("alert_trigger", FEATURE_WRITE),
    # --- templates -----------------------------------------------------------
    "routes.templates.get_default_templates": PUBLIC,
    "routes.templates.list_templates": READ,
    "routes.templates.get_template": READ,
    "routes.templates.create_template": _p("template_manage", FEATURE_WRITE),
    "routes.templates.delete_template": _p("template_manage", FEATURE_WRITE),
    # --- notifications (the caller's own) ------------------------------------
    "routes.notifications.list_notifications": SELF,
    "routes.notifications.unread_count": SELF,
    "routes.notifications.mark_as_read": SELF,
    "routes.notifications.mark_all_read": SELF,
    # --- billing -------------------------------------------------------------
    "routes.billing.get_plans": PUBLIC,
    "routes.billing.get_billing_config": PUBLIC,
    "routes.billing.stripe_webhook": PUBLIC,
    "routes.billing.get_subscription": READ,
    "routes.billing.get_capabilities": READ,
    "routes.billing.create_checkout_session": _p("billing_manage"),
    "routes.billing.create_billing_portal": _p("billing_manage"),
    # --- approval emails (token-authenticated links, flag-gated) --------------
    "routes.approvals.preview_email_action": PUBLIC,
    "routes.approvals.perform_email_action": PUBLIC,
    "routes.approvals.resend_status_webhook": PUBLIC,
    # --- agentic (flag-gated) --------------------------------------------------
    "routes.agentic.list_agents": READ,
    "routes.agentic.get_agent": READ,
    "routes.agentic.create_agent": _p("agent_manage", FEATURE_WRITE),
    "routes.agentic.update_agent": _p("agent_manage", FEATURE_WRITE),
    "routes.agentic.trigger_agent": _p("agent_manage", FEATURE_AI),
    "routes.agentic.delete_agent": _p("agent_delete", FEATURE_WRITE),
    "routes.agentic.get_risk_assessment": READ,
    "routes.agentic.get_organization_risk_summary": READ,
    "routes.agentic.assess_risk": _p("ai_task", FEATURE_WRITE),
    "routes.agentic.bulk_assess_organization": _p("ai_bulk", FEATURE_WRITE),
    "routes.agentic.list_playbooks": READ,
    "routes.agentic.get_playbook": READ,
    "routes.agentic.create_playbook": _p("playbook_manage", FEATURE_WRITE),
    "routes.agentic.update_playbook": _p("playbook_manage", FEATURE_WRITE),
    "routes.agentic.delete_playbook": _p("playbook_delete", FEATURE_WRITE),
    "routes.agentic.analyze_with_playbook": _p("redline_run", FEATURE_AI),
    "routes.agentic.get_redline_session": READ,
    "routes.agentic.get_contract_redline_sessions": READ,
    "routes.agentic.apply_redlines": _p("redline_apply", FEATURE_WRITE),
    "routes.agentic.resend_webhook": PUBLIC,
    "routes.agentic.list_intakes": READ,
    "routes.agentic.get_intake": READ,
    "routes.agentic.process_intake": _p("intake_process", FEATURE_WRITE),
    "routes.agentic.extract_obligations": _p("ai_task", FEATURE_WRITE),
    "routes.agentic.bulk_extract_obligations": _p("ai_bulk", FEATURE_WRITE),
    "routes.agentic.get_contract_obligations": READ,
    "routes.agentic.get_organization_obligations": READ,
    "routes.agentic.update_obligation_status": _p("obligation_update", FEATURE_WRITE),
    "routes.agentic.get_obligation_alerts": READ,
    "routes.agentic.acknowledge_alert": _p("obligation_update", FEATURE_WRITE),
}

MSG_ROUTE_NOT_PERMITTED = "This action is not permitted."


def endpoint_key(endpoint: Any) -> Optional[str]:
    """``module.function`` for a route endpoint, the key of ``ROUTE_POLICIES``."""
    if endpoint is None:
        return None
    return f"{endpoint.__module__}.{endpoint.__qualname__}"


def roles_with(capability: str) -> Tuple[str, ...]:
    """Roles (most privileged first) that hold ``capability`` outright."""
    return tuple(r for r in reversed(ROLES) if can(r, capability))


def _route_denial(role: str, policy: RoutePolicy) -> HTTPException:
    cap = policy.capability or policy.any_of[0]
    caps = (cap,) if policy.capability else policy.any_of
    if role == ROLE_VIEWER and any(c in CONTRACT_WRITE_CAPABILITIES for c in caps):
        detail = "Viewers cannot modify contracts"
    elif cap in _DENIAL_LABELS:
        detail = _DENIAL_LABELS[cap]
    else:
        allowed = [r for r in reversed(ROLES) if any(can(r, c) for c in caps)]
        detail = f"This action requires one of these roles: {', '.join(allowed)}"
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def route_allows(role: object, policy: RoutePolicy) -> bool:
    """Role half of a route decision (ownership is checked by the handler)."""
    if policy.public:
        return True
    normalized = normalize_role(role)
    if policy.capability and can(normalized, policy.capability):
        return True
    if any(can(normalized, c) for c in policy.any_of):
        return True
    return bool(
        policy.owner_scoped
        and policy.capability in OWNER_CAPABILITIES.get(normalized, frozenset())
    )


def authorize_endpoint(endpoint: Any, user: dict) -> None:
    """Enforce the role matrix for an authenticated request (deny by default).

    Called from ``utils.auth.get_current_user`` with the matched endpoint, so
    every authenticated route passes through it before the handler runs. An
    endpoint without a ``ROUTE_POLICIES`` row is refused.
    """
    key = endpoint_key(endpoint)
    policy = ROUTE_POLICIES.get(key) if key else None
    if policy is None:
        logger.error("No route policy for endpoint %s; denying by default", key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=MSG_ROUTE_NOT_PERMITTED
        )
    if not route_allows(user.get("role"), policy):
        raise _route_denial(normalize_role(user.get("role")), policy)
