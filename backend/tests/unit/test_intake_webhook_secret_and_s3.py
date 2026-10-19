"""Intake webhook: own secret (with deprecated fallback) + Svix verification;
one shared S3 bucket helper for storage and the /health probe."""
import base64
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from routes import agentic as agentic_route
from tests.unit.conftest import API_PREFIX
from utils import svix, webhook_secrets

BACKEND = Path(__file__).resolve().parents[2]
INTAKE_SECRET = "whsec_" + base64.b64encode(b"intake-secret-key-for-tests").decode()
LEGACY_SECRET = "whsec_" + base64.b64encode(b"legacy-status-secret-for-tests").decode()
OTHER_SECRET = "whsec_" + base64.b64encode(b"some-other-secret").decode()
URL = f"{API_PREFIX}/agentic/intake/webhook"
BODY = json.dumps({"type": "email.received", "data": {"from": "a@b.c", "subject": "Contract"}}).encode()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("RESEND_INTAKE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    webhook_secrets._reset_warning_for_tests()
    yield
    webhook_secrets._reset_warning_for_tests()


@pytest.fixture
def queued(monkeypatch):
    calls = []

    async def fake_enqueue(task, *args, **kwargs):
        calls.append(args)
        return SimpleNamespace(id="task-1")

    monkeypatch.setattr(agentic_route, "_enqueue", fake_enqueue)
    return calls


@pytest.fixture
async def http():
    app = FastAPI()
    api = APIRouter(prefix=API_PREFIX)
    api.include_router(agentic_route.build_router({"intake"}))
    app.include_router(api)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


def _headers(secret, body=BODY, ts=None, msg_id="msg_1", sig_override=None):
    ts = str(int(ts if ts is not None else time.time()))
    sig = sig_override or svix.sign(secret, msg_id, ts, body)
    return {"svix-id": msg_id, "svix-timestamp": ts, "svix-signature": sig, "content-type": "application/json"}


# --- secret resolution --------------------------------------------------------
def test_intake_secret_preferred(monkeypatch, caplog):
    monkeypatch.setenv("RESEND_INTAKE_WEBHOOK_SECRET", INTAKE_SECRET)
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", LEGACY_SECRET)
    with caplog.at_level(logging.WARNING):
        assert webhook_secrets.get_intake_webhook_secret() == INTAKE_SECRET
    assert "deprecated" not in caplog.text


def test_fallback_warns_once_without_secret(monkeypatch, caplog):
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", LEGACY_SECRET)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert webhook_secrets.get_intake_webhook_secret() == LEGACY_SECRET
    warnings = [r for r in caplog.records if "deprecated" in r.getMessage()]
    assert len(warnings) == 1
    assert LEGACY_SECRET not in caplog.text and LEGACY_SECRET.split("_", 1)[1] not in caplog.text


def test_no_secret_is_none():
    assert webhook_secrets.get_intake_webhook_secret() is None


# --- endpoint -------------------------------------------------------------------
async def test_missing_secret_rejects(http, queued):
    r = await http.post(URL, content=BODY, headers=_headers(INTAKE_SECRET))
    assert r.status_code == 503 and not queued


async def test_valid_signature_with_intake_secret(http, queued, monkeypatch):
    monkeypatch.setenv("RESEND_INTAKE_WEBHOOK_SECRET", INTAKE_SECRET)
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", LEGACY_SECRET)
    r = await http.post(URL, content=BODY, headers=_headers(INTAKE_SECRET))
    assert r.status_code == 200, r.text
    assert queued == [(json.loads(BODY),)]


async def test_status_secret_not_accepted_when_intake_secret_set(http, queued, monkeypatch):
    monkeypatch.setenv("RESEND_INTAKE_WEBHOOK_SECRET", INTAKE_SECRET)
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", LEGACY_SECRET)
    r = await http.post(URL, content=BODY, headers=_headers(LEGACY_SECRET))
    assert r.status_code == 401 and not queued


async def test_fallback_secret_accepted(http, queued, monkeypatch):
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", LEGACY_SECRET)
    r = await http.post(URL, content=BODY, headers=_headers(LEGACY_SECRET))
    assert r.status_code == 200 and len(queued) == 1


@pytest.mark.parametrize("case", ["tampered", "wrong_secret", "stale", "future", "v2_only", "missing_headers"])
async def test_bad_signatures_rejected(http, queued, monkeypatch, case):
    monkeypatch.setenv("RESEND_INTAKE_WEBHOOK_SECRET", INTAKE_SECRET)
    body, headers = BODY, _headers(INTAKE_SECRET)
    if case == "tampered":
        body = BODY.replace(b"Contract", b"Kontract")
    elif case == "wrong_secret":
        headers = _headers(OTHER_SECRET)
    elif case == "stale":
        headers = _headers(INTAKE_SECRET, ts=time.time() - 301)
    elif case == "future":
        headers = _headers(INTAKE_SECRET, ts=time.time() + 301)
    elif case == "v2_only":
        headers["svix-signature"] = headers["svix-signature"].replace("v1,", "v2,")
    elif case == "missing_headers":
        headers = {"content-type": "application/json"}
    r = await http.post(URL, content=body, headers=headers)
    assert r.status_code == 401 and not queued


async def test_multiple_signatures_one_valid(http, queued, monkeypatch):
    monkeypatch.setenv("RESEND_INTAKE_WEBHOOK_SECRET", INTAKE_SECRET)
    good = _headers(INTAKE_SECRET)
    bad = svix.sign(OTHER_SECRET, good["svix-id"], good["svix-timestamp"], BODY)
    good["svix-signature"] = f"{bad} v2,AAAA {good['svix-signature']}"
    r = await http.post(URL, content=BODY, headers=good)
    assert r.status_code == 200 and len(queued) == 1


def test_secret_must_be_valid_base64():
    with pytest.raises(svix.WebhookVerificationError):
        svix.verify("whsec_not*base64!", {"svix-id": "m", "svix-timestamp": str(int(time.time())),
                                          "svix-signature": "v1,AAAA"}, b"{}")


# --- S3 bucket helper + /health probe ---------------------------------------------
@pytest.mark.parametrize("env,expected", [
    ({"AWS_S3_BUCKET": "primary"}, "primary"),
    ({"S3_BUCKET_NAME": "legacy"}, "legacy"),
    ({"AWS_S3_BUCKET": "primary", "S3_BUCKET_NAME": "legacy"}, "primary"),
    ({}, None),
])
def test_get_bucket_name(monkeypatch, env, expected):
    from services import storage_service

    monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert storage_service.get_bucket_name() == expected


# server.py is imported in a subprocess: importing it in-process re-wires every
# route module's database handle and would leak into other tests.
PROBE = """
import asyncio, json
from services import storage_service
seen = {}
class FakeS3:
    def head_bucket(self, Bucket):
        seen["bucket"] = Bucket
storage_service.get_s3_client = lambda: FakeS3()
import server
print(json.dumps({"result": asyncio.run(server._probe_storage()), "bucket": seen.get("bucket")}))
"""


def _probe(**bucket_env):
    env = {k: v for k, v in os.environ.items() if k not in ("AWS_S3_BUCKET", "S3_BUCKET_NAME")}
    env.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production")
    env.setdefault("MONGO_URL", "mongodb://localhost:27017")
    env.update(bucket_env)
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=BACKEND, env=env,
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("var", ["AWS_S3_BUCKET", "S3_BUCKET_NAME"])
def test_health_probe_uses_either_bucket_var(var):
    out = _probe(**{var: "my-bucket"})
    assert out["result"]["status"] == "healthy"
    assert out["bucket"] == "my-bucket"


def test_health_probe_reports_unconfigured_bucket():
    out = _probe()
    assert out["result"] == {"status": "degraded", "details": "S3 bucket not configured"}
    assert out["bucket"] is None
