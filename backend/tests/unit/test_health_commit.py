"""/api/v1/health reports the deployed commit from RAILWAY_GIT_COMMIT_SHA."""
import sys

import pytest
from httpx import ASGITransport, AsyncClient

FULL_SHA = "b73dacb0addb9575f34627bbb619f4bb67c1374a"
EXPECTED_KEYS = {"status", "version", "checks", "timestamp", "commit"}


class _PingDB:
    async def command(self, name):
        return {"ok": 1}


def _import_server():
    """Import ``server`` without leaking its module-level DB handles.

    Same approach as test_ready_redis_and_worker: importing server wires every
    route module to the real Motor client, so restore those handles afterwards.
    """
    if "server" in sys.modules:
        return sys.modules["server"]
    before = {name: dict(vars(mod)) for name, mod in list(sys.modules.items())
              if mod is not None and name.startswith(("routes.", "services.", "utils."))}
    import server

    for name, snapshot in before.items():
        mod = sys.modules.get(name)
        if mod is None:
            continue
        for attr in ("db", "_db", "_users_collection"):
            if attr in snapshot:
                setattr(mod, attr, snapshot[attr])
    return server


@pytest.fixture
def health_server(monkeypatch):
    server = _import_server()

    async def _storage():
        return {"status": "degraded", "details": "Using mock storage"}

    monkeypatch.setattr(server, "db", _PingDB())
    monkeypatch.setattr(server, "_probe_storage", _storage)
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    return server


async def _get_health(server):
    async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://t") as c:
        r = await c.get("/api/v1/health")
    assert r.status_code == 200
    return r.json()


async def test_commit_is_short_sha_when_set(health_server, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", FULL_SHA)
    body = await _get_health(health_server)
    assert body["commit"] == "b73dacb"


async def test_commit_unknown_when_unset(health_server):
    body = await _get_health(health_server)
    assert body["commit"] == "unknown"


@pytest.mark.parametrize("value", ["", "   ", "\n"])
async def test_commit_unknown_when_empty(health_server, monkeypatch, value):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", value)
    body = await _get_health(health_server)
    assert body["commit"] == "unknown"


async def test_commit_is_read_per_request(health_server, monkeypatch):
    assert (await _get_health(health_server))["commit"] == "unknown"
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", FULL_SHA)
    assert (await _get_health(health_server))["commit"] == "b73dacb"


@pytest.mark.parametrize("value, expected", [
    (FULL_SHA, "b73dacb"),
    (f"  {FULL_SHA}\r\n", "b73dacb"),  # surrounding whitespace is stripped (utils.env.get_env)
    (FULL_SHA.upper(), "b73dacb"),     # uppercase hex is normalised
])
def test_commit_sha_helper(health_server, monkeypatch, value, expected):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", value)
    assert health_server._commit_sha() == expected


class _RaiseDB:
    def __init__(self, exc):
        self.exc = exc

    async def command(self, name):
        raise self.exc


class ServerSelectionTimeoutError(Exception):
    pass


async def test_health_database_timeout_stays_degraded(health_server, monkeypatch):
    monkeypatch.setattr(
        health_server,
        "db",
        _RaiseDB(ServerSelectionTimeoutError("secret-host.mongodb.net")),
    )
    body = await _get_health(health_server)
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == {
        "status": "unhealthy",
        "details": "Database probe timed out",
    }
    assert "secret-host" not in str(body)


async def test_health_database_error_is_unreachable(health_server, monkeypatch):
    monkeypatch.setattr(
        health_server,
        "db",
        _RaiseDB(RuntimeError("auth failed for secret-db-user")),
    )
    body = await _get_health(health_server)
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["details"] == "Database unreachable"
    assert "secret-db-user" not in str(body)


async def test_existing_health_fields_unchanged(health_server, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", FULL_SHA)
    body = await _get_health(health_server)
    assert set(body) == EXPECTED_KEYS
    assert body["status"] == "healthy"
    assert body["version"] == "2.0.0"
    assert isinstance(body["timestamp"], float)
    assert set(body["checks"]) == {"database", "storage", "ai", "email"}
    assert body["checks"]["database"] == {"status": "healthy", "details": "Connected"}


@pytest.mark.parametrize("value", [
    "abc12",                      # too short
    "not-a-sha",                  # not hex
    "b73dacb0addb9575f34627bbb619f4bb67c1374a0",  # longer than 40
    "b73dacb 0addb",              # embedded whitespace
    "<script>alert(1)</script>",
    "\u00e9" * 10,                # non-ASCII
])
async def test_commit_unknown_when_malformed(health_server, monkeypatch, value):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", value)
    assert health_server._commit_sha() == "unknown"
    body = await _get_health(health_server)
    assert body["commit"] == "unknown"


async def test_commit_lookup_error_never_fails_health(health_server, monkeypatch):
    class _BoomRe:
        def fullmatch(self, *_a, **_k):
            raise RuntimeError("sha check failed")

    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", FULL_SHA)
    monkeypatch.setattr(health_server, "_COMMIT_SHA_RE", _BoomRe())
    assert health_server._commit_sha() == "unknown"
    body = await _get_health(health_server)  # asserts 200
    assert body["commit"] == "unknown"
