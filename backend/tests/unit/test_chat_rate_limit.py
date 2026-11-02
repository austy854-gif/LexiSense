"""Per-user and per-org rate limit on contract chat (B5)."""
import pytest

from routes import contracts as contracts_route
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract, make_user
from utils import rbac


@pytest.fixture
def chat_ok(monkeypatch):
    calls = []

    async def fake_chat(text, question, session_id):
        calls.append(question)
        return "answer"

    monkeypatch.setattr(contracts_route, "get_chat_response", fake_chat)
    return calls


@pytest.fixture
def small_limits(monkeypatch):
    monkeypatch.setattr(contracts_route, "CHAT_USER_LIMITER", rbac._InMemoryAsyncLimiter(2, 60))
    monkeypatch.setattr(contracts_route, "CHAT_ORG_LIMITER", rbac._InMemoryAsyncLimiter(3, 60))


async def _ask(client, user, cid="c-chat"):
    return await client.post(f"{API_PREFIX}/contracts/{cid}/chat", json={"question": "Q?"}, headers=auth_headers(user))


@pytest.fixture
async def chat_contract(db, org_id, owner):
    await db.contracts.insert_one(make_contract(org_id, owner["id"], contract_id="c-chat", originalText="Term."))


async def test_per_user_limit(client, users, chat_contract, chat_ok, small_limits):
    assert [(await _ask(client, users["user"])).status_code for _ in range(3)] == [200, 200, 429]
    resp = await _ask(client, users["user"])
    assert resp.status_code == 429 and "your account" in resp.json()["detail"]
    assert int(resp.headers["Retry-After"]) >= 1
    assert len(chat_ok) == 2


async def test_per_org_limit_across_users(client, users, chat_contract, chat_ok, small_limits):
    codes = [(await _ask(client, users[r])).status_code for r in ("user", "user", "manager", "admin")]
    assert codes == [200, 200, 200, 429]
    assert "your organization" in (await _ask(client, users["viewer"])).json()["detail"]


async def test_limits_are_scoped_per_org(client, db, users, other_org_id, chat_contract, chat_ok, small_limits):
    for _ in range(3):
        await _ask(client, users[("user", "manager", "admin")[_]])
    await db.contracts.insert_one(make_contract(other_org_id, "o-1", contract_id="c-other", originalText="T."))
    outsider = make_user(other_org_id, role="admin", user_id="o-1")
    assert (await _ask(client, outsider, "c-other")).status_code == 200


async def test_unauthenticated_does_not_consume_budget(client, users, chat_contract, chat_ok, small_limits):
    for _ in range(5):
        r = await client.post(f"{API_PREFIX}/contracts/c-chat/chat", json={"question": "Q?"})
        assert r.status_code in (401, 403)
    assert (await _ask(client, users["user"])).status_code == 200


async def test_redis_down_fails_open_with_local_budget(client, users, chat_contract, chat_ok, monkeypatch):
    """Redis unreachable -> circuit breaker + in-process fallback (same budget)."""
    limiter = rbac.RedisRateLimiter("redis://127.0.0.1:1/0", 2, 60, prefix="rl-test", connect_timeout=0.05,
                                    socket_timeout=0.05)
    monkeypatch.setattr(contracts_route, "CHAT_USER_LIMITER", limiter)
    monkeypatch.setattr(contracts_route, "CHAT_ORG_LIMITER", rbac._InMemoryAsyncLimiter(100, 60))
    codes = [(await _ask(client, users["user"])).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert limiter.breaker_open


def test_chat_limits_are_config_driven(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_LIMIT_PER_USER_PER_MINUTE", "7")
    assert contracts_route._env_int("CHAT_RATE_LIMIT_PER_USER_PER_MINUTE", 10) == 7
    monkeypatch.setenv("CHAT_RATE_LIMIT_PER_USER_PER_MINUTE", "nope")
    assert contracts_route._env_int("CHAT_RATE_LIMIT_PER_USER_PER_MINUTE", 10) == 10
