"""Readiness Redis probe, Celery broker polling options, worker launch flags."""
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
WORKER_FLAGS = ("--without-gossip", "--without-mingle", "--without-heartbeat")


# --- Celery conf ------------------------------------------------------------
def test_broker_transport_options_polling_interval_and_preserved():
    probe = ("import json; from services.celery_app import celery_app as c; "
             "print(json.dumps(c.conf.broker_transport_options))")
    env = {k: v for k, v in os.environ.items() if k not in ("REDIS_URL", "CELERY_BROKER_URL")}
    env.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production")
    out = subprocess.run([sys.executable, "-c", probe], cwd=BACKEND, env=env,
                         capture_output=True, text=True, check=True)
    opts = json.loads(out.stdout.strip().splitlines()[-1])
    assert opts["polling_interval"] == 10
    assert "socket_timeout" in opts and "socket_connect_timeout" in opts


# --- Worker launch commands -------------------------------------------------
def _worker_commands():
    cmds = []
    for path in [*REPO.glob("docker-compose*.yml"), *REPO.glob("Procfile"), *REPO.glob("railway.*"),
                 *REPO.glob("**/Dockerfile"), *REPO.glob("*.sh"), *BACKEND.glob("scripts/*")]:
        if "node_modules" in path.parts or not path.is_file():
            continue
        for line in path.read_text(errors="ignore").splitlines():
            if re.search(r"celery\s+(-A|--app)\s+\S+\s+worker\b", line) and not line.lstrip().startswith("#"):
                cmds.append((path.name, line))
    return cmds


def test_worker_commands_disable_gossip_mingle_heartbeat():
    cmds = _worker_commands()
    assert cmds, "no celery worker command found"
    for name, line in cmds:
        for flag in WORKER_FLAGS:
            assert flag in line, f"{name}: worker command missing {flag}"


# --- /api/v1/ready ------------------------------------------------------------
class _PingDB:
    async def command(self, name):
        return {"ok": 1}


def _import_server():
    """Import ``server`` without leaking its side effects into other tests.

    Importing it calls every route module's ``init_db`` with the real Motor
    client (and routes.auth wires utils.auth._users_collection); snapshot and
    restore those module-level handles afterwards.
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
def ready_app(monkeypatch):
    server = _import_server()

    monkeypatch.setattr(server, "db", _PingDB())
    monkeypatch.setattr(server.app.state, "bootstrap_ok", True, raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    return server


async def _get_ready(server):
    async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://t") as c:
        return await c.get("/api/v1/ready")


class _FakeRedis:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.closed = False

    async def ping(self):
        if self.behaviour == "ok":
            return True
        if self.behaviour == "hang":
            await asyncio.sleep(10)
        raise ConnectionError("boom")

    async def aclose(self):
        self.closed = True


def _patch_redis(monkeypatch, behaviour):
    import redis.asyncio as aioredis

    fake = _FakeRedis(behaviour)
    monkeypatch.setattr(aioredis, "from_url", lambda url, **kw: fake)
    return fake


async def test_ready_redis_skipped_when_unset(ready_app):
    r = await _get_ready(ready_app)
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["redis"] == "skipped"
    assert body["checks"]["database"] is True
    assert body["checks"]["database_status"] == "ok"


def test_database_probe_waits_for_server_selection(ready_app):
    """The probe budget must cover the driver's server-selection timeout.

    A shorter asyncio timeout was the production failure mode: /ready
    returned 503 with database false, and /health said the probe timed out,
    while pymongo was still selecting a server.
    """
    timeout_s = ready_app._database_probe_timeout_seconds()
    selection_s = ready_app.SERVER_SELECTION_TIMEOUT_MS / 1000.0
    assert timeout_s >= selection_s + ready_app._DB_PROBE_MARGIN_SECONDS - 1e-9


class _SlowDB:
    def __init__(self, delay):
        self.delay = delay

    async def command(self, name):
        await asyncio.sleep(self.delay)
        return {"ok": 1}


class _RaiseDB:
    def __init__(self, exc):
        self.exc = exc

    async def command(self, name):
        raise self.exc


class ServerSelectionTimeoutError(Exception):
    """Stand-in with the driver's exception name. The probe classifies by name."""


async def test_ready_slow_ping_inside_driver_budget_is_up(ready_app, monkeypatch):
    monkeypatch.setattr(ready_app, "db", _SlowDB(0.05))
    monkeypatch.setattr(ready_app, "_database_probe_timeout_seconds", lambda: 0.5)
    r = await _get_ready(ready_app)
    assert r.status_code == 200
    checks = r.json()["checks"]
    assert checks["database"] is True
    assert checks["database_status"] == "ok"


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError(),
        ServerSelectionTimeoutError("timed out contacting secret-host.mongodb.net"),
    ],
)
async def test_ready_database_timeout_is_named(ready_app, monkeypatch, caplog, exc):
    monkeypatch.setattr(ready_app, "db", _RaiseDB(exc))
    r = await _get_ready(ready_app)
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["database"] is False
    assert checks["database_status"] == "timeout"
    assert "secret-host" not in r.text
    assert "secret-host" not in caplog.text


async def test_ready_database_error_hides_driver_text(ready_app, monkeypatch, caplog):
    monkeypatch.setattr(
        ready_app,
        "db",
        _RaiseDB(RuntimeError("auth failed for user secret-db-user")),
    )
    r = await _get_ready(ready_app)
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["database"] is False
    assert checks["database_status"] == "error"
    assert "secret-db-user" not in r.text
    assert "secret-db-user" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_ready_redis_ping_ok(ready_app, monkeypatch):
    monkeypatch.setenv("REDIS_URL", "rediss://default:secret@example.upstash.io:6379")
    fake = _patch_redis(monkeypatch, "ok")
    r = await _get_ready(ready_app)
    assert r.status_code == 200 and r.json()["checks"]["redis"] == "healthy"
    assert fake.closed


@pytest.mark.parametrize("behaviour", ["fail", "hang"])
async def test_ready_redis_ping_failure_is_503(ready_app, monkeypatch, caplog, behaviour):
    monkeypatch.setenv("CELERY_BROKER_URL", "rediss://default:secret@example.upstash.io:6379")
    monkeypatch.setattr(ready_app, "REDIS_PROBE_TIMEOUT_SECONDS", 0.1)
    _patch_redis(monkeypatch, behaviour)
    r = await _get_ready(ready_app)
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready" and body["checks"]["redis"] == "unhealthy"
    assert "secret" not in caplog.text and "upstash" not in caplog.text
    assert "secret" not in r.text
