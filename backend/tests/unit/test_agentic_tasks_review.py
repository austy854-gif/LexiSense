"""Cindy's PR #31 review: tenant scoping in tasks, pagination, task DB
contextvar, and the Redis limiter circuit breaker / fail-closed bulk path."""
import os

for _var in ("REDIS_URL", "RATE_LIMIT_REDIS_URL", "CELERY_BROKER_URL"):
    os.environ.pop(_var, None)

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from services import obligation_tasks, risk_tasks, task_utils  # noqa: E402
from services.task_db import current_db  # noqa: E402
from tests.unit.conftest import make_contract  # noqa: E402
from utils import rbac  # noqa: E402

TEXT = "The Supplier shall deliver the report within 30 days. Payment is due within 45 days. Unlimited liability applies."


@pytest.fixture
def task_db(db):
    token = current_db.set(db)
    yield db
    current_db.reset(token)


async def _seed_contract(db, org, cid, **extra):
    doc = make_contract(org, "u-1", contract_id=cid, **extra)
    doc.setdefault("extractedText", TEXT)
    doc["extractedText"] = doc["originalText"] = TEXT
    doc["status"] = extra.get("status", "active")
    await db.contracts.insert_one(doc)
    return doc


async def _snapshot(db):
    out = {}
    for name in ("contracts", "risk_assessments", "extracted_obligations", "obligation_extraction_jobs", "obligation_alerts"):
        out[name] = await getattr(db, name).find({}, {"_id": 0}).to_list(None)
    return out


# ------------------------------------------------------------ task DB context
def test_get_db_prefers_the_contextvar(db):
    assert risk_tasks.get_db() is not db
    token = current_db.set(db)
    try:
        assert risk_tasks.get_db() is db and obligation_tasks.get_db() is db
    finally:
        current_db.reset(token)


# ------------------------------------------------------------ tenant scoping
async def test_risk_task_with_mismatched_org_changes_nothing(task_db):
    await _seed_contract(task_db, "org-alpha", "c-1")
    before = await _snapshot(task_db)
    result = await risk_tasks._assess_contract_risk_async("c-1", True, "org-beta")
    assert result["status"] == "error"
    assert await _snapshot(task_db) == before


async def test_obligation_task_with_mismatched_org_changes_nothing(task_db):
    await _seed_contract(task_db, "org-alpha", "c-1")
    before = await _snapshot(task_db)
    result = await obligation_tasks._extract_obligations_from_contract_async("c-1", True, "org-beta")
    assert result["status"] == "error"
    assert await _snapshot(task_db) == before


async def test_tasks_require_an_org(task_db):
    await _seed_contract(task_db, "org-alpha", "c-1")
    assert (await risk_tasks._assess_contract_risk_async("c-1", True))["status"] == "error"
    assert (await obligation_tasks._extract_obligations_from_contract_async("c-1", True))["status"] == "error"


async def test_risk_task_with_matching_org_writes_scoped_docs(task_db):
    await _seed_contract(task_db, "org-alpha", "c-1")
    result = await risk_tasks._assess_contract_risk_async("c-1", True, "org-alpha")
    assert result["status"] == "success", result
    docs = await task_db.risk_assessments.find({}).to_list(None)
    assert docs and all(d["organizationId"] == "org-alpha" for d in docs)


async def test_cleanup_is_scoped_per_org(task_db):
    await _seed_contract(task_db, "org-alpha", "c-a")
    await _seed_contract(task_db, "org-beta", "c-b")
    old = "2000-01-01T00:00:00+00:00"
    # An orphan alert with no organisation must not be touched by org-scoped cleanup.
    await task_db.obligation_alerts.insert_many([
        {"id": "a1", "organizationId": "org-alpha", "sent_at": old, "acknowledged": True},
        {"id": "a2", "organizationId": "org-beta", "sent_at": old, "acknowledged": False},
        {"id": "a3", "sent_at": old, "acknowledged": True},
    ])
    result = await obligation_tasks._cleanup_completed_obligations_async()
    assert result["alerts_deleted"] == 1
    assert sorted(a["id"] for a in await task_db.obligation_alerts.find({}).to_list(None)) == ["a2", "a3"]


# ------------------------------------------------------------- pagination
async def test_iter_batches_covers_every_doc(db):
    await db.contracts.insert_many([{"id": f"c-{i:04d}", "organizationId": "o"} for i in range(1234)])
    seen, sizes = [], []
    async for batch in task_utils.iter_batches(db.contracts, {"organizationId": "o"}, {"_id": 0, "id": 1}, batch_size=200):
        sizes.append(len(batch))
        seen.extend(d["id"] for d in batch)
    assert len(seen) == len(set(seen)) == 1234
    assert max(sizes) == 200


async def test_bulk_assess_queues_past_the_old_500_cap(task_db, monkeypatch):
    await task_db.contracts.insert_many(
        [{"id": f"c-{i:04d}", "organizationId": "org-alpha", "status": "active"} for i in range(650)]
        + [{"id": f"x-{i}", "organizationId": "org-beta", "status": "active"} for i in range(5)]
    )
    calls = []
    monkeypatch.setattr(risk_tasks.assess_contract_risk, "delay", lambda *a, **k: calls.append(a))
    result = await risk_tasks._bulk_assess_organization_contracts_async("org-alpha")
    assert result["queued"] == 650
    assert len({c[0] for c in calls}) == 650 and all(c[2] == "org-alpha" for c in calls)


async def test_bulk_extract_queues_past_the_old_200_cap(task_db, monkeypatch):
    await task_db.contracts.insert_many(
        [{"id": f"c-{i:04d}", "organizationId": "org-alpha", "status": "active"} for i in range(450)]
    )
    calls = []
    monkeypatch.setattr(obligation_tasks.extract_obligations_from_contract, "delay", lambda *a, **k: calls.append(a))
    result = await obligation_tasks._bulk_extract_obligations_async("org-alpha")
    assert result["queued"] == 450 and all(c[2] == "org-alpha" for c in calls)


# ------------------------------------------------------- Redis limiter breaker
class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _BrokenPipe:
    def __init__(self, counter):
        self.counter = counter

    def incr(self, *_):
        pass

    def expire(self, *_):
        pass

    async def execute(self):
        self.counter.append(1)
        raise ConnectionError("redis down")


def _broken_limiter(fail_closed, clock):
    lim = rbac.RedisRateLimiter("redis://127.0.0.1:1/0", 2, 60, fail_closed=fail_closed, clock=clock)
    attempts = []
    lim._redis = type("R", (), {"pipeline": lambda self: _BrokenPipe(attempts)})()
    return lim, attempts


def test_redis_client_timeouts_are_sub_second():
    lim = rbac.RedisRateLimiter("redis://127.0.0.1:1/0", 1, 60)
    kw = lim._redis.connection_pool.connection_kwargs
    assert kw["socket_timeout"] < 1 and kw["socket_connect_timeout"] < 1


async def test_breaker_skips_redis_for_30s_then_retries():
    clock = _Clock()
    lim, attempts = _broken_limiter(False, clock)
    assert (await lim.acheck("k"))[0] is True       # fallback after failure
    assert lim.breaker_open and len(attempts) == 1
    for _ in range(5):
        await lim.acheck("other")
    assert len(attempts) == 1                       # Redis skipped while open
    clock.t += 31
    await lim.acheck("k")
    assert len(attempts) == 2                       # half-open retry after ~30s


async def test_fallback_still_enforces_budget():
    lim, _ = _broken_limiter(False, _Clock())
    results = [(await lim.acheck("k"))[0] for _ in range(3)]
    assert results == [True, True, False]


async def test_bulk_limiter_fails_closed_with_503():
    lim, _ = _broken_limiter(True, _Clock())
    with pytest.raises(rbac.RateLimiterUnavailable):
        await lim.acheck("k")
    dep = rbac.org_rate_limit(lim, "agentic:bulk:risk", user_dependency=lambda: None)
    with pytest.raises(HTTPException) as exc:
        await dep(current_user={"organizationId": "org-alpha"})
    assert exc.value.status_code == 503


def test_make_org_limiter_fail_closed_only_with_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("RATE_LIMIT_REDIS_URL", raising=False)
    assert not isinstance(rbac.make_org_limiter(1, 60, "rl", fail_closed=True), rbac.RedisRateLimiter)
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", "redis://127.0.0.1:1/0")
    lim = rbac.make_org_limiter(1, 60, "rl", fail_closed=True)
    assert isinstance(lim, rbac.RedisRateLimiter) and lim.fail_closed
