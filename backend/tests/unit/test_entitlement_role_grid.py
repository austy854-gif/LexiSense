"""Entitlement grid and role matrix: every API route, every role, every billing state.

Two tables decide who may call what:

* ``utils.policy.ROUTE_POLICIES`` -- the role matrix. One row per endpoint
  (``module.function``) naming the capability it needs; ``CAPABILITIES`` maps
  each role to its capabilities. ``utils.auth.get_current_user`` enforces the
  row for every authenticated request and refuses (403) an endpoint that has
  no row, so a new route is denied until someone classifies it.
* ``utils.billing_access.ENTITLEMENT_GRID`` -- billing state -> features
  (upload, ai, write, invite). Each route's row names the feature its handler
  enforces. Every paid plan gets the same features; plans differ only in their
  seat cap (``utils.plans``).

Both denials are 403, matching the existing billing gates and the SPA's error
handling (no route returns 402).

The tests below check:

1. coverage -- every mountable route has exactly one row, public rows are
   exactly the unauthenticated routes, and each row's feature matches the
   guard its handler calls;
2. the role matrix itself (pinned, so a change is deliberate);
3. deny by default for an unlisted endpoint;
4. the role grid -- every authenticated route x every role against a real
   in-process app (Business plan, so seat caps never interfere);
5. the entitlement grid -- every feature-gated route x every billing state;
6. routes with no feature (reads, billing, team management) keep working when
   the workspace is read-only, so a cancelled org can still pay or leave;
7. seat caps per plan.
"""

import inspect
import io
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from routes import (
    agentic as agentic_route,
    alerts as alerts_route,
    analytics as analytics_route,
    approvals as approvals_route,
    audit as audit_route,
    auth as auth_route,
    billing as billing_route,
    contracts as contracts_route,
    dashboard as dashboard_route,
    export as export_route,
    notifications as notifications_route,
    tags as tags_route,
    team as team_route,
    templates as templates_route,
    workflow as workflow_route,
)
from services import audit_service, email_service
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract, make_user
from utils import billing_access as ba
from utils import plans as plan_config
from utils import policy
from utils.auth import get_current_user
from utils.feature_flags import ROUTE_GROUP_FLAGS

ORG = "org-grid"
ROLES = ("viewer", "user", "manager", "admin")
NOW = datetime.now(timezone.utc)
PRICES = {
    "STRIPE_PRICE_STARTER_MONTHLY": "price_starter_m",
    "STRIPE_PRICE_TEAM_MONTHLY": "price_team_m",
    "STRIPE_PRICE_BUSINESS_MONTHLY": "price_business_m",
}

#: Routers server.py can mount (agentic with every group, approval emails on).
#: ``routes.reports`` is deliberately absent: it is never mounted (deferred for
#: launch), so it has no policy rows and would be denied if it were.
ROUTERS = [
    auth_route.router,
    contracts_route.router,
    team_route.router,
    dashboard_route.router,
    alerts_route.router,
    templates_route.router,
    export_route.router,
    analytics_route.router,
    audit_route.router,
    notifications_route.router,
    workflow_route.router,
    tags_route.router,
    billing_route.router,
    approvals_route.router,
]
SERVER_ENDPOINTS = ("server.root", "server.health_check", "server.readiness_check")


def _iso(**delta):
    return (NOW + timedelta(**delta)).isoformat()


def _all_routes():
    routers = ROUTERS + [agentic_route.build_router(set(ROUTE_GROUP_FLAGS))]
    for router in routers:
        for route in router.routes:
            if isinstance(route, APIRoute):
                yield route


def _uses_current_user(dependant) -> bool:
    for sub in dependant.dependencies:
        if sub.call is get_current_user or _uses_current_user(sub):
            return True
    return False


ROUTES = {policy.endpoint_key(r.endpoint): r for r in _all_routes()}


# ---------------------------------------------------------------------------
# 1. Coverage
# ---------------------------------------------------------------------------
def test_endpoint_keys_are_unique():
    keys = [policy.endpoint_key(r.endpoint) for r in _all_routes()]
    assert len(keys) == len(set(keys))


def test_every_route_has_exactly_one_policy_row():
    missing = sorted(set(ROUTES) - set(policy.ROUTE_POLICIES))
    assert not missing, f"routes without a policy row (they would 403): {missing}"


def _server_functions():
    """Function names defined in server.py, read without importing it (an
    import wires module-level database handles that would leak into other
    tests)."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[2] / "server.py").read_text())
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_no_policy_row_points_at_a_missing_route():
    server_functions = _server_functions()
    stale = sorted(
        k
        for k in set(policy.ROUTE_POLICIES) - set(ROUTES)
        if not (k in SERVER_ENDPOINTS and k.split(".", 1)[1] in server_functions)
    )
    assert not stale, f"policy rows for routes that do not exist: {stale}"


@pytest.mark.parametrize("key", sorted(ROUTES))
def test_public_rows_are_exactly_the_unauthenticated_routes(key):
    authenticated = _uses_current_user(ROUTES[key].dependant)
    assert policy.ROUTE_POLICIES[key].public is not authenticated, (
        f"{key}: policy says {'public' if not authenticated else 'authenticated'} "
        "but the route disagrees"
    )


#: The guard a handler calls for each entitlement feature.
FEATURE_GUARDS = {
    ba.FEATURE_WRITE: "assert_org_writable",
    ba.FEATURE_AI: "ensure_ai_access",
    ba.FEATURE_INVITE: "ensure_invite_access",
    ba.FEATURE_UPLOAD: "reserve_trial_upload_atomic",
}


@pytest.mark.parametrize("key", sorted(ROUTES))
def test_feature_rows_match_the_guard_the_handler_calls(key):
    source = inspect.getsource(ROUTES[key].endpoint)
    if key == "routes.contracts.upload_contract":
        # Single upload reserves through a helper shared with bulk upload.
        source += inspect.getsource(contracts_route)
    feature = policy.ROUTE_POLICIES[key].feature
    called = {f for f, guard in FEATURE_GUARDS.items() if f"{guard}(" in source}
    if feature is None:
        assert not called, f"{key} enforces {called} but its row has no feature"
    else:
        assert (
            feature in called
        ), f"{key} row says {feature!r} but the handler never checks it"


# ---------------------------------------------------------------------------
# 2. The role matrix
# ---------------------------------------------------------------------------
_MEMBER = {"read", "self", "ai_chat"}
_CONTRACT_WRITES = {
    "upload", "delete", "restore", "version", "submit_for_review", "approve",
    "reject", "activate", "contract_edit", "tag_manage",
}  # fmt: skip
_AGENTIC_MANAGER = {
    "agent_manage", "playbook_manage", "redline_run", "redline_apply",
    "intake_process", "ai_task", "ai_bulk", "obligation_update",
}  # fmt: skip
EXPECTED_MATRIX = {
    "viewer": _MEMBER,
    "user": _MEMBER
    | {"upload", "submit_for_review", "redline_run", "ai_task", "obligation_update"},
    "manager": _MEMBER | _CONTRACT_WRITES | _AGENTIC_MANAGER
    | {"audit_view", "alert_trigger"},
    "admin": _MEMBER | _CONTRACT_WRITES | _AGENTIC_MANAGER
    | {
        "audit_view", "alert_trigger", "alert_settings", "template_manage",
        "invite", "team_manage", "billing_manage", "agent_delete", "playbook_delete",
    },
}  # fmt: skip


@pytest.mark.parametrize("role", ROLES)
def test_role_matrix_is_pinned(role):
    assert set(policy.CAPABILITIES[role]) == EXPECTED_MATRIX[role]


def test_roles_are_strictly_nested():
    for lower, higher in zip(ROLES, ROLES[1:]):
        assert EXPECTED_MATRIX[lower] <= EXPECTED_MATRIX[higher], (lower, higher)


@pytest.mark.parametrize("role", ["", "owner", "superadmin", None, 7])
def test_unknown_roles_get_viewer_rights_only(role):
    for key, row in policy.ROUTE_POLICIES.items():
        assert policy.route_allows(role, row) == policy.route_allows("viewer", row), key


def test_every_policy_capability_is_held_by_some_role():
    for key, row in policy.ROUTE_POLICIES.items():
        for cap in filter(None, (row.capability, *row.any_of)):
            assert policy.roles_with(cap), f"{key}: nobody holds {cap!r}"


# ---------------------------------------------------------------------------
# 3. Deny by default
# ---------------------------------------------------------------------------
async def _unlisted_endpoint(current_user: dict = Depends(get_current_user)):
    return {"ok": True}


async def test_an_authenticated_route_without_a_row_is_denied(
    db, db_authoritative_auth
):
    admin = make_user(ORG, role="admin")
    await db.users.insert_one(dict(admin))
    app = FastAPI()
    app.add_api_route("/unlisted", _unlisted_endpoint, methods=["GET"])
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/unlisted", headers=auth_headers(admin))
    assert response.status_code == 403
    assert response.json()["detail"] == policy.MSG_ROUTE_NOT_PERMITTED


def test_entitlement_grid_denies_unknown_states_and_features():
    assert not ba.entitlement_allows("something_new", ba.FEATURE_WRITE)
    assert not ba.entitlement_allows(ba.ACCESS_PAID, "teleport")


# ---------------------------------------------------------------------------
# Shared in-process app with every router mounted
# ---------------------------------------------------------------------------
BILLING_STATES = {
    ba.ACCESS_PAID: {
        "subscriptionTier": "team",
        "billingStatus": "active",
        "stripePriceId": "price_business_m",
    },
    ba.ACCESS_GRACE: {
        "subscriptionTier": "team",
        "billingStatus": "past_due",
        "stripePriceId": "price_team_m",
        "pastDueSince": _iso(days=-1),
    },
    ba.ACCESS_TRIAL: {
        "subscriptionTier": "trial",
        "billingStatus": "trialing",
        "trialEndDate": _iso(days=5),
    },
    ba.ACCESS_TRIAL_EXPIRED: {
        "subscriptionTier": "trial",
        "billingStatus": "trialing",
        "trialEndDate": _iso(days=-1),
    },
    ba.ACCESS_PAST_DUE: {
        "subscriptionTier": "team",
        "billingStatus": "past_due",
        "stripePriceId": "price_team_m",
        "stripeCustomerId": "cus_grid",
        "pastDueSince": _iso(days=-30),
    },
    ba.ACCESS_CANCELLED: {
        "subscriptionTier": "cancelled",
        "billingStatus": "cancelled",
    },
    ba.ACCESS_UNVERIFIED_PLAN: {
        "subscriptionTier": "team",
        "billingStatus": "active",
        "stripePriceId": "price_not_configured",
    },
}


_USERS_CACHE = {}


def _grid_users():
    """One account per role, built once: bcrypt makes ``make_user`` slow."""
    if not _USERS_CACHE:
        _USERS_CACHE.update(
            {role: make_user(ORG, role=role, user_id=f"u-{role}") for role in ROLES}
        )
    return _USERS_CACHE


@pytest.fixture
def prices(monkeypatch):
    for name in plan_config.STRIPE_PRICE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in PRICES.items():
        monkeypatch.setenv(name, value)
    for name in ("REDIS_URL", "RATE_LIMIT_REDIS_URL", "CELERY_BROKER_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
async def grid(db, db_authoritative_auth, monkeypatch, prices):
    for module in (
        contracts_route, team_route, billing_route, dashboard_route, alerts_route,
        templates_route, export_route, analytics_route, audit_route,
        notifications_route, workflow_route, tags_route, agentic_route,
        audit_service,
    ):  # fmt: skip
        module.init_db(db)

    async def _no_email(*args, **kwargs):
        return {"id": "test-email", "status": "sent"}

    monkeypatch.setattr(email_service, "send_invitation_email", _no_email)
    monkeypatch.setattr(team_route, "send_invitation_email", _no_email)
    agentic_route.AGENTIC_TASK_LIMITER.reset()
    agentic_route.AGENTIC_BULK_LIMITER.reset()

    app = FastAPI()
    api = APIRouter(prefix=API_PREFIX)
    for router in ROUTERS:
        if router is not approvals_route.router:  # public, token-link routes
            api.include_router(router)
    api.include_router(agentic_route.build_router(set(ROUTE_GROUP_FLAGS)))
    app.include_router(api)

    users = {role: dict(u) for role, u in _grid_users().items()}
    await db.users.insert_many([dict(u) for u in users.values()])
    await db.organizations.insert_one({"id": ORG, "name": "Grid Co"})
    await db.contracts.insert_one(
        make_contract(ORG, users["user"]["id"], contract_id="c-1", status="draft")
    )
    await db.contract_versions.insert_one(
        {
            "id": "v-1",
            "contractId": "c-1",
            "organizationId": ORG,
            "version": 1,
            "title": "Master Services Agreement",
            "status": "draft",
            "changedBy": users["user"]["id"],
            "changedAt": _iso(),
        }
    )
    await db.invitations.insert_one(
        {
            "id": "inv-1",
            "organizationId": ORG,
            "email": "pending@example.com",
            "role": "user",
            "status": "pending",
            "expiresAt": _iso(days=3),
            "createdAt": _iso(),
            "invitedBy": users["admin"]["id"],
        }
    )
    await db.templates.insert_one(
        {"id": "t-1", "organizationId": ORG, "name": "NDA", "content": "x"}
    )
    await db.contract_agents.insert_one(
        {
            "id": "ag-1",
            "organizationId": ORG,
            "name": "Renewals",
            "createdBy": "u-admin",
        }
    )
    await db.legal_playbooks.insert_one(
        {"id": "pb-1", "organizationId": ORG, "name": "Default", "createdBy": "u-admin"}
    )
    await db.redline_sessions.insert_one(
        {"id": "rs-1", "organizationId": ORG, "contractId": "c-1"}
    )
    await db.contract_intakes.insert_one({"id": "in-1", "organizationId": ORG})
    await db.extracted_obligations.insert_one(
        {"id": "ob-1", "organizationId": ORG, "contractId": "c-1", "status": "pending"}
    )
    await db.obligation_alerts.insert_one(
        {"id": "al-1", "organizationId": ORG, "acknowledged": False}
    )
    await db.notifications.insert_one(
        {"id": "n-1", "organizationId": ORG, "userId": "u-viewer", "read": False}
    )

    async def set_billing(state):
        await db.organization_billing.delete_many({"organizationId": ORG})
        await db.organization_billing.insert_one(
            {
                "id": "b-grid",
                "organizationId": ORG,
                "trialContractsUsed": 0,
                "trialContractsLimit": 25,
                "trialEndDate": _iso(days=5),
                **BILLING_STATES[state],
            }
        )
        raw = await db.organization_billing.find_one({"organizationId": ORG})
        assert ba.compute_entitlement(raw).state == state

    await set_billing(ba.ACCESS_PAID)  # Business plan: 30 seats
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "db": db, "users": users, "set_billing": set_billing}


def _pdf():
    return ("msa.txt", io.BytesIO(b"This agreement renews annually."), "text/plain")


#: A valid request for every authenticated route: (method, path, kwargs).
REQUESTS = {
    "routes.auth.get_current_user_info": ("GET", "/auth/me", {}),
    "routes.auth.logout": ("POST", "/auth/logout", {}),
    "routes.contracts.list_contracts": ("GET", "/contracts", {}),
    "routes.contracts.get_contract": ("GET", "/contracts/c-1", {}),
    "routes.contracts.get_contract_text": ("GET", "/contracts/c-1/text", {}),
    "routes.contracts.download_contract_file": ("GET", "/contracts/c-1/file", {}),
    "routes.contracts.get_contract_versions": ("GET", "/contracts/c-1/versions", {}),
    "routes.contracts.get_contract_version": ("GET", "/contracts/c-1/versions/1", {}),
    "routes.contracts.upload_contract": (
        "POST", "/contracts", {"files": {"file": _pdf}, "data": {"title": "MSA"}},
    ),
    "routes.contracts.bulk_upload_contracts": (
        "POST", "/contracts/bulk", {"files": [("files", _pdf)]},
    ),
    "routes.contracts.chat_with_contract": (
        "POST", "/contracts/c-1/chat", {"json": {"question": "When does it renew?"}},
    ),
    "routes.contracts.update_contract": (
        "PATCH", "/contracts/c-1", {"json": {"title": "Renamed"}},
    ),
    "routes.contracts.restore_contract_version": (
        "POST", "/contracts/c-1/restore/1", {},
    ),
    "routes.contracts.delete_contract": ("DELETE", "/contracts/c-1", {}),
    "routes.workflow.get_workflow_states": ("GET", "/contracts/workflow/states", {}),
    "routes.workflow.get_workflow_history": (
        "GET", "/contracts/c-1/workflow/history", {},
    ),
    "routes.workflow.workflow_action": (
        "POST", "/contracts/c-1/workflow/submit_for_review", {},
    ),
    "routes.tags.list_organization_tags": ("GET", "/tags", {}),
    "routes.tags.add_tags": ("POST", "/tags/c-1", {"json": ["vendor"]}),
    "routes.tags.remove_tag": ("DELETE", "/tags/c-1/vendor", {}),
    "routes.tags.update_category": (
        "PATCH", "/tags/c-1/category", {"params": {"category": "Vendors"}},
    ),
    "routes.team.list_team_members": ("GET", "/team/members", {}),
    "routes.team.invite_member": (
        "POST", "/team/invite", {"json": {"email": "new@example.com", "role": "user"}},
    ),
    "routes.team.list_invitations": ("GET", "/team/invitations", {}),
    "routes.team.cancel_invitation": ("DELETE", "/team/invitations/inv-1", {}),
    "routes.team.update_member_role": (
        "PATCH", "/team/members/u-viewer/role", {"json": {"role": "user"}},
    ),
    "routes.team.remove_member": ("DELETE", "/team/members/u-viewer", {}),
    "routes.dashboard.get_dashboard_stats": ("GET", "/dashboard/stats", {}),
    "routes.dashboard.get_recent_activity": ("GET", "/dashboard/activity", {}),
    "routes.analytics.get_analytics_overview": ("GET", "/analytics/overview", {}),
    "routes.analytics.compare_contracts": (
        "GET", "/analytics/contracts/c-1/compare/c-1", {},
    ),
    "routes.export.export_contract_pdf": ("GET", "/export/contract/c-1/pdf", {}),
    "routes.export.export_analytics_pdf": ("GET", "/export/analytics/pdf", {}),
    "routes.audit.list_audit_logs": ("GET", "/audit", {}),
    "routes.alerts.get_alert_settings": ("GET", "/alerts/settings", {}),
    "routes.alerts.get_expiring_contracts": ("GET", "/alerts/expiring", {}),
    "routes.alerts.get_alert_history": ("GET", "/alerts/history", {}),
    "routes.alerts.update_alert_settings": (
        "PUT", "/alerts/settings", {"params": {"emailEnabled": "false"}},
    ),
    "routes.alerts.check_and_send_alerts": ("POST", "/alerts/check-and-send", {}),
    "routes.templates.list_templates": ("GET", "/templates", {}),
    "routes.templates.get_template": ("GET", "/templates/t-1", {}),
    "routes.templates.create_template": (
        "POST", "/templates", {"json": {"name": "MSA", "content": "Terms"}},
    ),
    "routes.templates.delete_template": ("DELETE", "/templates/t-1", {}),
    "routes.notifications.list_notifications": ("GET", "/notifications", {}),
    "routes.notifications.unread_count": ("GET", "/notifications/unread-count", {}),
    "routes.notifications.mark_as_read": ("POST", "/notifications/n-1/read", {}),
    "routes.notifications.mark_all_read": ("POST", "/notifications/read-all", {}),
    "routes.billing.get_subscription": ("GET", "/billing/subscription", {}),
    "routes.billing.get_capabilities": ("GET", "/billing/capabilities", {}),
    "routes.billing.create_checkout_session": (
        "POST",
        "/billing/checkout",
        {
            "json": {
                "planId": "team",
                "successUrl": "https://app.test/ok",
                "cancelUrl": "https://app.test/no",
            }
        },
    ),
    "routes.billing.create_billing_portal": (
        "POST", "/billing/portal", {"json": {"returnUrl": "https://app.test/billing"}},
    ),
    "routes.agentic.list_agents": ("GET", "/agentic/agents", {}),
    "routes.agentic.get_agent": ("GET", "/agentic/agents/ag-1", {}),
    "routes.agentic.create_agent": (
        "POST", "/agentic/agents", {"json": {"name": "A", "agentType": "expiration_monitor"}},
    ),
    "routes.agentic.update_agent": (
        "PATCH", "/agentic/agents/ag-1", {"json": {"name": "Renamed"}},
    ),
    "routes.agentic.trigger_agent": ("POST", "/agentic/agents/ag-1/trigger", {}),
    "routes.agentic.delete_agent": ("DELETE", "/agentic/agents/ag-1", {}),
    "routes.agentic.get_risk_assessment": ("GET", "/agentic/risk/c-1", {}),
    "routes.agentic.get_organization_risk_summary": (
        "GET", "/agentic/risk/organization/summary", {},
    ),
    "routes.agentic.assess_risk": (
        "POST", "/agentic/risk/assess", {"json": {"contractId": "c-1"}},
    ),
    "routes.agentic.bulk_assess_organization": (
        "POST", "/agentic/risk/bulk-assess", {},
    ),
    "routes.agentic.list_playbooks": ("GET", "/agentic/playbooks", {}),
    "routes.agentic.get_playbook": ("GET", "/agentic/playbooks/pb-1", {}),
    "routes.agentic.create_playbook": (
        "POST", "/agentic/playbooks", {"json": {"name": "Vendor"}},
    ),
    "routes.agentic.update_playbook": (
        "PATCH", "/agentic/playbooks/pb-1", {"json": {"name": "Renamed"}},
    ),
    "routes.agentic.delete_playbook": ("DELETE", "/agentic/playbooks/pb-1", {}),
    "routes.agentic.analyze_with_playbook": (
        "POST",
        "/agentic/redline/analyze",
        {"json": {"contractId": "c-1", "playbookId": "pb-1"}},
    ),
    "routes.agentic.get_redline_session": ("GET", "/agentic/redline/sessions/rs-1", {}),
    "routes.agentic.get_contract_redline_sessions": (
        "GET", "/agentic/redline/contract/c-1/sessions", {},
    ),
    "routes.agentic.apply_redlines": (
        "POST",
        "/agentic/redline/sessions/rs-1/apply",
        {"json": {"sessionId": "rs-1", "suggestionIds": []}},
    ),
    "routes.agentic.list_intakes": ("GET", "/agentic/intake", {}),
    "routes.agentic.get_intake": ("GET", "/agentic/intake/in-1", {}),
    "routes.agentic.process_intake": (
        "POST", "/agentic/intake/in-1/process", {"json": {"intakeId": "in-1"}},
    ),
    "routes.agentic.extract_obligations": (
        "POST", "/agentic/obligations/extract", {"json": {"contractId": "c-1"}},
    ),
    "routes.agentic.bulk_extract_obligations": (
        "POST", "/agentic/obligations/bulk-extract", {},
    ),
    "routes.agentic.get_contract_obligations": (
        "GET", "/agentic/obligations/contract/c-1", {},
    ),
    "routes.agentic.get_organization_obligations": (
        "GET", "/agentic/obligations/organization", {},
    ),
    "routes.agentic.update_obligation_status": (
        "PATCH",
        "/agentic/obligations/ob-1/status",
        {"json": {"obligationId": "ob-1", "status": "completed"}},
    ),
    "routes.agentic.get_obligation_alerts": ("GET", "/agentic/obligations/alerts", {}),
    "routes.agentic.acknowledge_alert": (
        "POST", "/agentic/obligations/alerts/al-1/acknowledge", {},
    ),
}  # fmt: skip

AUTHENTICATED = sorted(k for k, row in policy.ROUTE_POLICIES.items() if not row.public)


def test_every_authenticated_route_has_a_grid_request():
    assert sorted(REQUESTS) == AUTHENTICATED


async def _call(grid, key, role):
    method, path, kwargs = REQUESTS[key]
    kwargs = dict(kwargs)
    if "files" in kwargs:  # fresh file objects per request
        files = kwargs["files"]
        kwargs["files"] = (
            [(name, make()) for name, make in files]
            if isinstance(files, list)
            else {name: make() for name, make in files.items()}
        )
    return await grid["client"].request(
        method,
        f"{API_PREFIX}{path}",
        headers=auth_headers(grid["users"][role]),
        **kwargs,
    )


def _role_expected(key, role):
    if key == "routes.workflow.workflow_action":
        return policy.can(role, "submit_for_review")
    row = policy.ROUTE_POLICIES[key]
    # Owner-scoped rows: the grid contract was uploaded by the ``user`` account.
    return policy.route_allows(role, row)


# ---------------------------------------------------------------------------
# 4. Role grid
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("key", AUTHENTICATED)
async def test_role_grid(grid, key, role):
    response = await _call(grid, key, role)
    if _role_expected(key, role):
        assert response.status_code not in (
            401,
            403,
            422,
        ), f"{role} should reach {key}: {response.status_code} {response.text[:200]}"
    else:
        assert (
            response.status_code == 403
        ), f"{role} must be refused {key}: {response.status_code} {response.text[:200]}"


@pytest.mark.parametrize(
    "action,status,allowed",
    [
        ("submit_for_review", "draft", {"user", "manager", "admin"}),
        ("approve", "review", {"manager", "admin"}),
        ("reject", "review", {"manager", "admin"}),
        ("activate", "approved", {"manager", "admin"}),
    ],
)
@pytest.mark.parametrize("role", ROLES)
async def test_workflow_action_grid(grid, role, action, status, allowed):
    await grid["db"].contracts.update_one({"id": "c-1"}, {"$set": {"status": status}})
    response = await grid["client"].post(
        f"{API_PREFIX}/contracts/c-1/workflow/{action}",
        headers=auth_headers(grid["users"][role]),
    )
    if role in allowed:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 403, response.text
        stored = await grid["db"].contracts.find_one({"id": "c-1"})
        assert stored["status"] == status


@pytest.mark.parametrize(
    "key", [k for k in AUTHENTICATED if policy.ROUTE_POLICIES[k].owner_scoped]
)
async def test_user_cannot_write_someone_elses_contract(grid, key):
    await grid["db"].contracts.update_one(
        {"id": "c-1"}, {"$set": {"uploadedBy": grid["users"]["manager"]["id"]}}
    )
    response = await _call(grid, key, "user")
    assert response.status_code == 403, response.text


async def test_viewer_denials_do_not_touch_data(grid):
    before = await grid["db"].contracts.find_one({"id": "c-1"}, {"_id": 0})
    for key in AUTHENTICATED:
        if not _role_expected(key, "viewer"):
            assert (await _call(grid, key, "viewer")).status_code == 403, key
    after = await grid["db"].contracts.find_one({"id": "c-1"}, {"_id": 0})
    assert after == before
    assert await grid["db"].templates.count_documents({}) == 1
    assert await grid["db"].invitations.count_documents({}) == 1
    assert await grid["db"].users.count_documents({}) == 4


# ---------------------------------------------------------------------------
# 5. Entitlement grid
# ---------------------------------------------------------------------------
FEATURE_ROUTES = sorted(k for k in AUTHENTICATED if policy.ROUTE_POLICIES[k].feature)


def test_entitlement_grid_table():
    full = {ba.FEATURE_UPLOAD, ba.FEATURE_AI, ba.FEATURE_WRITE, ba.FEATURE_INVITE}
    expected = {
        ba.ACCESS_PAID: full,
        ba.ACCESS_GRACE: full,
        ba.ACCESS_TRIAL: full,
        ba.ACCESS_TRIAL_EXPIRED: {ba.FEATURE_WRITE, ba.FEATURE_INVITE},
        ba.ACCESS_PAST_DUE: set(),
        ba.ACCESS_CANCELLED: set(),
        ba.ACCESS_UNVERIFIED_PLAN: set(),
    }
    assert {s: set(f) for s, f in ba.ENTITLEMENT_GRID.items()} == expected
    assert set(BILLING_STATES) == set(expected)


@pytest.mark.parametrize("state", sorted(BILLING_STATES))
def test_grid_agrees_with_the_entitlement_record(state, prices):
    """``Entitlement.can_use_ai`` and READ_ONLY_STATES say the same as the grid."""
    raw = {"organizationId": ORG, **BILLING_STATES[state]}
    ent = ba.compute_entitlement(raw)
    assert ent.state == state
    assert ent.can_use_ai == ba.entitlement_allows(state, ba.FEATURE_AI)
    assert (state in ba.READ_ONLY_STATES) == (
        not ba.entitlement_allows(state, ba.FEATURE_WRITE)
    )


@pytest.mark.parametrize("state", sorted(BILLING_STATES))
@pytest.mark.parametrize("key", FEATURE_ROUTES)
async def test_entitlement_grid(grid, key, state):
    await grid["set_billing"](state)
    # Keep the org at one member so the trial seat cap never decides an invite.
    await grid["db"].users.delete_many({"id": {"$ne": grid["users"]["admin"]["id"]}})
    feature = policy.ROUTE_POLICIES[key].feature
    response = await _call(grid, key, "admin")
    if ba.entitlement_allows(state, feature):
        assert response.status_code not in (401, 403, 422), (
            f"{state} should allow {feature} on {key}: "
            f"{response.status_code} {response.text[:200]}"
        )
    elif key == "routes.contracts.bulk_upload_contracts":
        # Bulk upload reports per-file results; every file is refused.
        body = response.json()
        assert response.status_code == 200 and body["successful"] == 0, body
        assert body["results"]["failed"][0]["error"] == ba.blocked_message(state)
    else:
        assert response.status_code == 403, (
            f"{state} must block {feature} on {key}: "
            f"{response.status_code} {response.text[:200]}"
        )


async def test_blocked_writes_change_nothing(grid):
    await grid["set_billing"](ba.ACCESS_CANCELLED)
    before = await grid["db"].contracts.find_one({"id": "c-1"}, {"_id": 0})
    for key in FEATURE_ROUTES:
        response = await _call(grid, key, "admin")
        if key == "routes.contracts.bulk_upload_contracts":
            assert response.json()["successful"] == 0, key
        else:
            assert response.status_code == 403, key
    assert await grid["db"].contracts.find_one({"id": "c-1"}, {"_id": 0}) == before
    assert await grid["db"].contracts.count_documents({}) == 1
    assert await grid["db"].invitations.count_documents({}) == 1


# ---------------------------------------------------------------------------
# 6. Routes without a feature keep working when read-only
# ---------------------------------------------------------------------------
UNGATED = sorted(k for k in AUTHENTICATED if not policy.ROUTE_POLICIES[k].feature)


@pytest.mark.parametrize("state", [ba.ACCESS_CANCELLED, ba.ACCESS_PAST_DUE])
@pytest.mark.parametrize("key", UNGATED)
async def test_ungated_routes_work_when_read_only(grid, key, state):
    await grid["set_billing"](state)
    response = await _call(grid, key, "admin")
    assert response.status_code not in (
        401,
        403,
        422,
    ), f"{key} must stay usable when {state}: {response.status_code} {response.text[:200]}"


async def test_capabilities_endpoint_reports_the_grid_row(grid):
    await grid["set_billing"](ba.ACCESS_TRIAL_EXPIRED)
    body = (await _call(grid, "routes.billing.get_capabilities", "admin")).json()
    assert body["state"] == ba.ACCESS_TRIAL_EXPIRED
    assert body["features"] == {
        "upload": False,
        "ai": False,
        "write": True,
        "invite": True,
    }
    assert body["readOnly"] is False


# ---------------------------------------------------------------------------
# 7. Seat caps per plan
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "state,price,cap",
    [
        (ba.ACCESS_TRIAL, None, 3),
        (ba.ACCESS_PAID, "price_starter_m", 5),
        (ba.ACCESS_PAID, "price_team_m", 15),
        (ba.ACCESS_PAID, "price_business_m", 30),
    ],
)
async def test_seat_cap_per_plan(grid, state, price, cap):
    await grid["set_billing"](state)
    if price:
        await grid["db"].organization_billing.update_one(
            {"organizationId": ORG}, {"$set": {"stripePriceId": price}}
        )
    assert await billing_route.member_limit_for_org(ORG) == cap
    db = grid["db"]
    await db.users.delete_many({"id": {"$ne": grid["users"]["admin"]["id"]}})
    await db.invitations.delete_many({})
    filler = _grid_users()["viewer"]
    await db.users.insert_many(
        [
            {**filler, "id": f"seat-{i}", "email": f"seat-{i}@example.com"}
            for i in range(cap - 2)
        ]
    )
    ok = await _call(grid, "routes.team.invite_member", "admin")
    assert ok.status_code not in (401, 403, 422), ok.text  # last free seat
    full = await grid["client"].post(
        f"{API_PREFIX}/team/invite",
        headers=auth_headers(grid["users"]["admin"]),
        json={"email": "one-too-many@example.com", "role": "user"},
    )
    assert full.status_code == 403
    assert f"up to {cap} team members" in full.json()["detail"]


# ---------------------------------------------------------------------------
# Playbook analysis honours the playbook's approval setting
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "require_approval,requested,expected",
    [(True, True, False), (False, True, True), (False, False, False)],
)
async def test_auto_apply_needs_a_playbook_without_approval(
    grid, monkeypatch, require_approval, requested, expected
):
    from services import celery_app as celery_module

    calls = []

    async def fake_threadpool(fn, *args, **kwargs):
        calls.append(kwargs["args"])
        return type("Task", (), {"id": "task-1"})()

    monkeypatch.setattr(celery_module, "CELERY_BROKER_URL", "redis://broker:6379/0")
    monkeypatch.setattr(agentic_route, "run_in_threadpool", fake_threadpool)
    await grid["db"].legal_playbooks.update_one(
        {"id": "pb-1"}, {"$set": {"require_approval": require_approval}}
    )
    response = await grid["client"].post(
        f"{API_PREFIX}/agentic/redline/analyze",
        headers=auth_headers(grid["users"]["admin"]),
        json={"contractId": "c-1", "playbookId": "pb-1", "auto_apply": requested},
    )
    assert response.status_code == 200, response.text
    assert calls == [("c-1", "pb-1", expected)]
