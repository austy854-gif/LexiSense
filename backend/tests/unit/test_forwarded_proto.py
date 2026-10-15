"""ForwardedProtoMiddleware (X-Forwarded-Proto hop trust) and request-log client identity."""
import logging

import pytest
from fastapi import APIRouter, FastAPI, Request, WebSocket
from fastapi.testclient import TestClient

from utils.logging_config import RequestLoggingMiddleware
from utils.proxy import ForwardedProtoMiddleware


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FORWARDED_PROTO_HOP", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)


def _app():
    app = FastAPI()
    router = APIRouter(prefix="/api/v1")

    @router.get("/things/")
    async def things():
        return {"ok": True}

    @router.get("/scheme")
    async def scheme(request: Request):
        return {"scheme": request.url.scheme}

    app.include_router(router)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text(websocket.scope["scheme"])
        await websocket.close()

    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(ForwardedProtoMiddleware)
    return app


def _location(xfp_lines):
    client = TestClient(_app(), base_url="http://app.test")
    headers = [("x-forwarded-proto", v) for v in xfp_lines]
    resp = client.get("/api/v1/things", headers=headers, follow_redirects=False)
    assert resp.status_code == 307
    return resp.headers["location"]


@pytest.mark.parametrize("count,xfp_lines,expected", [
    ("1", ["https"], "https"),
    ("1", ["HTTPS "], "https"),             # case/whitespace normalised
    ("1", ["https, http"], "http"),         # rightmost (proxy-added) wins
    ("1", ["http, https"], "https"),        # client-prefilled left entry ignored
    ("2", ["https"], "http"),               # fewer entries than hops -> unchanged
    ("2", ["https, http"], "https"),
    ("2", ["https", "http"], "https"),      # repeated header lines joined in order
    ("0", ["https"], "http"),               # trust disabled
    ("1", ["javascript"], "http"),          # only http/https
    ("1", ["wss"], "http"),
    ("1", [""], "http"),
    ("1", [" , "], "http"),
    ("abc", ["https"], "https"),            # invalid count falls back to 1
])
def test_redirect_location(monkeypatch, count, xfp_lines, expected):
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", count)
    assert _location(xfp_lines).startswith(f"{expected}://app.test/api/v1/things/")


@pytest.mark.parametrize("count,hop,xfp_lines,expected", [
    ("2", None, ["https, http"], "https"),  # default follows TRUSTED_PROXY_COUNT
    ("2", None, ["https"], "http"),
    ("2", "1", ["https"], "https"),         # LB overwrote XFP: single value, hop 1
    ("2", "1", ["http, https"], "https"),
    ("1", "0", ["https"], "http"),          # 0 = no-op
    ("2", "abc", ["https, http"], "https"),  # invalid -> default (2)
    ("2", "abc", ["https"], "http"),
    ("2", "-1", ["https"], "http"),         # negative -> default (2)
    ("2", "", ["https"], "http"),           # empty -> default
])
def test_forwarded_proto_hop(monkeypatch, count, hop, xfp_lines, expected):
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", count)
    if hop is not None:
        monkeypatch.setenv("FORWARDED_PROTO_HOP", hop)
    assert _location(xfp_lines).startswith(f"{expected}://")


def test_websocket_scheme(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    client = TestClient(_app())
    with client.websocket_connect("/ws", headers={"x-forwarded-proto": "https"}) as ws:
        assert ws.receive_text() == "wss"
    with client.websocket_connect("/ws", headers={"x-forwarded-proto": "http"}) as ws:
        assert ws.receive_text() == "ws"
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_text() == "ws"


def test_request_log_uses_client_key_and_redacts_token(monkeypatch, caplog):
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    caplog.set_level(logging.INFO)
    TestClient(_app()).get("/api/v1/scheme?token=SECRETTOKEN123",
                           headers={"x-forwarded-for": "6.6.6.6, 203.0.113.5"})
    app_records = [r for r in caplog.records if r.name.startswith("lexisense")]
    assert app_records
    text = " ".join(str(r.__dict__) for r in app_records)
    assert "SECRETTOKEN123" not in text
    assert "203.0.113.5" in text
    assert "6.6.6.6" not in text
