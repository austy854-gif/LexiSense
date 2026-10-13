"""Phase 1 agentic routes: feature flags, org isolation, RBAC, 503 and 429.

The routers run in-process against mongomock; Celery is never contacted. The
enqueue path is exercised up to ``run_in_threadpool(task.apply_async, ...)``,
which is replaced with a stub that records the call.
"""
import os
from types import SimpleNamespace

# Use the in-process limiter (and no real broker) regardless of the caller's env.
for _var in ("REDIS_URL", "RATE_LIMIT_REDIS_URL", "CELERY_BROKER_URL"):
    os.environ.pop(_var, None)

import pytest  # noqa: E402
from fastapi import APIRouter, FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from routes import agentic as agentic_route  # noqa: E402
from services import celery_app as celery_module  # noqa: E402
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract, make_user  # noqa: E402

PHASE1 = {"risk", "obligations"}


def _app(groups):
    app = FastAPI()
    api = APIRouter(prefix=API_PREFIX)
    if groups:
        api.include_router(agentic_route.build_router(groups))
    app.include_router(api)
    return app


@pytest.fixture
def queued(monkeypatch):
    """Pretend a broker is configured and capture enqueued tasks."""
    calls = []

    async def fake_threadpool(fn, *args, **kwargs):
        calls.append((getattr(fn, "__self__", None), kwargs))
        return SimpleNamespace(id="task-123")

    monkeypatch.setattr(celery_module, "CELERY_BROKER_URL", "redis://broker:6379/0")
    monkeypatch.setattr(agentic_route, "run_in_threadpool", fake_threadpool)
    return calls


@pytest.fixture(autouse=True)
def _reset_agentic_limiters():
    agentic_route.AGENTIC_TASK_LIMITER.reset()
    agentic_route.AGENTIC_BULK_LIMITER.reset()
    yield
    agentic_route.AGENTIC_TASK_LIMITER.reset()
    agentic_route.AGENTIC_BULK_LIMITER.reset()


@pytest.fixture
async def phase1(db):
    agentic_route.init_db(db)
    transport = ASGITransport(app=_app(PHASE1))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def alice(org_id):
    return make_user(org_id, role="user", user_id="alice")


@pytest.fixture
def mallory(other_org_id):
    return make_user(other_org_id, role="admin", user_id="mallory")


@pytest.fixture
async def seeded(db, org_id, alice):
    await db.contracts.insert_one(make_contract(org_id, alice["id"], contract_id="c-1"))
    await db.extracted_obligations.insert_one(
        {"id": "ob-1", "contractId": "c-1", "organizationId": org_id, "status": "pending", "title": "Pay"}
    )
    await db.obligation_alerts.insert_one(
        {"id": "al-1", "organizationId": org_id, "acknowledged": False}
    )


# --------------------------------------------------------------------------- flags
def test_enabled_groups_default_off(monkeypatch):
    for flag in set(agentic_route.ROUTE_GROUP_FLAGS.values()):
        monkeypatch.delenv(flag, raising=False)
    assert agentic_route.enabled_groups() == set()


def test_phase1_flag_enables_only_risk_and_obligations(monkeypatch):
    monkeypatch.setenv("AGENTIC_PHASE1_ENABLED", "true")
    for flag in ("AGENTIC_AGENTS_ENABLED", "AGENTIC_INTAKE_ENABLED", "AGENTIC_REDLINE_ENABLED"):
        monkeypatch.delenv(flag, raising=False)
    assert agentic_route.enabled_groups() == PHASE1


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/agentic/risk/assess"),
        ("get", "/agentic/risk/organization/summary"),
        ("post", "/agentic/obligations/extract"),
        ("post", "/agentic/agents"),
        ("post", "/agentic/intake/webhook"),
        ("get", "/agentic/playbooks"),
        ("post", "/agentic/redline/analyze"),
        ("get", "/agentic/health"),
    ],
)
async def test_flags_off_routes_404(method, path, alice):
    transport = ASGITransport(app=_app(set()))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await getattr(client, method)(API_PREFIX + path, headers=auth_headers(alice))
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/agentic/agents"),
        ("post", "/agentic/intake/webhook"),
        ("get", "/agentic/playbooks"),
        ("post", "/agentic/redline/analyze"),
        ("get", "/agentic/health"),
    ],
)
async def test_phase1_does_not_mount_other_groups(phase1, method, path, alice):
    resp = await getattr(phase1, method)(API_PREFIX + path, headers=auth_headers(alice))
    assert resp.status_code == 404


# --------------------------------------------------------------- org isolation
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/agentic/risk/assess", {"contractId": "c-1"}),
        ("get", "/agentic/risk/c-1", None),
        ("post", "/agentic/obligations/extract", {"contractId": "c-1"}),
        ("get", "/agentic/obligations/contract/c-1", None),
        ("patch", "/agentic/obligations/ob-1/status", {"obligationId": "ob-1", "status": "completed"}),
        ("post", "/agentic/obligations/alerts/al-1/acknowledge", None),
    ],
)
async def test_cross_org_access_is_404(phase1, seeded, queued, mallory, method, path, body):
    kwargs = {"headers": auth_headers(mallory)}
    if body is not None:
        kwargs["json"] = body
    resp = await getattr(phase1, method)(API_PREFIX + path, **kwargs)
    assert resp.status_code == 404
    assert queued == []


async def test_cross_org_writes_do_not_modify(phase1, seeded, db, mallory):
    await phase1.patch(
        f"{API_PREFIX}/agentic/obligations/ob-1/status",
        json={"obligationId": "ob-1", "status": "completed"},
        headers=auth_headers(mallory),
    )
    await phase1.post(f"{API_PREFIX}/agentic/obligations/alerts/al-1/acknowledge", headers=auth_headers(mallory))
    assert (await db.extracted_obligations.find_one({"id": "ob-1"}))["status"] == "pending"
    assert (await db.obligation_alerts.find_one({"id": "al-1"}))["acknowledged"] is False


async def test_same_org_status_update_and_ack(phase1, seeded, db, alice):
    r = await phase1.patch(
        f"{API_PREFIX}/agentic/obligations/ob-1/status",
        json={"obligationId": "ob-1", "status": "completed"},
        headers=auth_headers(alice),
    )
    assert r.status_code == 200
    r = await phase1.post(f"{API_PREFIX}/agentic/obligations/alerts/al-1/acknowledge", headers=auth_headers(alice))
    assert r.status_code == 200
    assert (await db.extracted_obligations.find_one({"id": "ob-1"}))["status"] == "completed"
    assert (await db.obligation_alerts.find_one({"id": "al-1"}))["acknowledged"] is True


async def test_same_org_assess_and_extract_are_queued(phase1, seeded, queued, alice):
    r = await phase1.post(f"{API_PREFIX}/agentic/risk/assess", json={"contractId": "c-1"}, headers=auth_headers(alice))
    assert r.status_code == 200
    r = await phase1.post(f"{API_PREFIX}/agentic/obligations/extract", json={"contractId": "c-1"}, headers=auth_headers(alice))
    assert r.status_code == 200 and r.json()["task_id"] == "task-123"
    assert len(queued) == 2


# ------------------------------------------------------------------------ RBAC
@pytest.mark.parametrize("path", ["/agentic/risk/bulk-assess", "/agentic/obligations/bulk-extract"])
@pytest.mark.parametrize("role,expected", [("viewer", 403), ("user", 403), ("manager", 200), ("admin", 200)])
async def test_bulk_requires_admin_or_manager(phase1, queued, users, path, role, expected):
    resp = await phase1.post(API_PREFIX + path, headers=auth_headers(users[role]))
    assert resp.status_code == expected


async def test_unauthenticated_is_rejected(phase1):
    resp = await phase1.post(f"{API_PREFIX}/agentic/risk/assess", json={"contractId": "c-1"})
    assert resp.status_code in (401, 403)


# ------------------------------------------------------------------------- 503
@pytest.mark.parametrize(
    "path,body,role",
    [
        ("/agentic/risk/assess", {"contractId": "c-1"}, "user"),
        ("/agentic/obligations/extract", {"contractId": "c-1"}, "user"),
        ("/agentic/risk/bulk-assess", None, "admin"),
        ("/agentic/obligations/bulk-extract", None, "admin"),
    ],
)
async def test_no_broker_returns_503(phase1, seeded, monkeypatch, org_id, path, body, role):
    monkeypatch.setattr(celery_module, "CELERY_BROKER_URL", None)
    user = make_user(org_id, role=role)
    kwargs = {"headers": auth_headers(user)}
    if body is not None:
        kwargs["json"] = body
    resp = await phase1.post(API_PREFIX + path, **kwargs)
    assert resp.status_code == 503


async def test_broker_down_returns_503(phase1, seeded, monkeypatch, alice):
    monkeypatch.setattr(celery_module, "CELERY_BROKER_URL", "redis://broker:6379/0")

    async def boom(*args, **kwargs):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(agentic_route, "run_in_threadpool", boom)
    resp = await phase1.post(f"{API_PREFIX}/agentic/risk/assess", json={"contractId": "c-1"}, headers=auth_headers(alice))
    assert resp.status_code == 503


# ------------------------------------------------------------------------- 429
async def test_task_rate_limit_is_per_org(phase1, seeded, queued, monkeypatch, alice, mallory):
    limit = agentic_route.AGENTIC_TASK_LIMITER._inner.max_requests
    for _ in range(limit):
        r = await phase1.post(f"{API_PREFIX}/agentic/risk/assess", json={"contractId": "c-1"}, headers=auth_headers(alice))
        assert r.status_code == 200
    r = await phase1.post(f"{API_PREFIX}/agentic/obligations/extract", json={"contractId": "c-1"}, headers=auth_headers(alice))
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    # Another organisation has its own budget.
    r = await phase1.post(f"{API_PREFIX}/agentic/risk/assess", json={"contractId": "c-1"}, headers=auth_headers(mallory))
    assert r.status_code == 404


async def test_bulk_rate_limit(phase1, queued, users):
    limit = agentic_route.AGENTIC_BULK_LIMITER._inner.max_requests
    for _ in range(limit):
        r = await phase1.post(f"{API_PREFIX}/agentic/risk/bulk-assess", headers=auth_headers(users["admin"]))
        assert r.status_code == 200
    r = await phase1.post(f"{API_PREFIX}/agentic/risk/bulk-assess", headers=auth_headers(users["manager"]))
    assert r.status_code == 429


async def test_bulk_budgets_are_separate_per_feature(phase1, queued, users):
    """agentic:bulk:risk and agentic:bulk:obligations are independent budgets."""
    limit = agentic_route.AGENTIC_BULK_LIMITER._inner.max_requests
    for _ in range(limit):
        r = await phase1.post(f"{API_PREFIX}/agentic/risk/bulk-assess", headers=auth_headers(users["admin"]))
        assert r.status_code == 200
    assert (await phase1.post(f"{API_PREFIX}/agentic/risk/bulk-assess", headers=auth_headers(users["admin"]))).status_code == 429
    for _ in range(limit):
        r = await phase1.post(f"{API_PREFIX}/agentic/obligations/bulk-extract", headers=auth_headers(users["admin"]))
        assert r.status_code == 200
    r = await phase1.post(f"{API_PREFIX}/agentic/obligations/bulk-extract", headers=auth_headers(users["admin"]))
    assert r.status_code == 429


async def test_single_tasks_are_enqueued_with_the_callers_org(phase1, seeded, queued, alice, org_id):
    await phase1.post(f"{API_PREFIX}/agentic/risk/assess", json={"contractId": "c-1"}, headers=auth_headers(alice))
    await phase1.post(f"{API_PREFIX}/agentic/obligations/extract", json={"contractId": "c-1"}, headers=auth_headers(alice))
    assert [kw["args"][-1] for _, kw in queued] == [org_id, org_id]


async def test_forbidden_callers_do_not_consume_bulk_budget(phase1, queued, users):
    limit = agentic_route.AGENTIC_BULK_LIMITER._inner.max_requests
    for _ in range(limit + 2):
        r = await phase1.post(f"{API_PREFIX}/agentic/risk/bulk-assess", headers=auth_headers(users["viewer"]))
        assert r.status_code == 403
    r = await phase1.post(f"{API_PREFIX}/agentic/risk/bulk-assess", headers=auth_headers(users["admin"]))
    assert r.status_code == 200
