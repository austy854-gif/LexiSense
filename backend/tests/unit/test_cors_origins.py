"""CORS_ORIGINS rejects wildcard origins before the process serves traffic."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cors_origins import CorsOriginError, load_cors_origins, parse_cors_origins

BACKEND = Path(__file__).resolve().parents[2]


def test_exact_origins_are_kept_and_trimmed():
    assert parse_cors_origins(
        " https://app.lexisense.net , http://localhost:3000 , "
    ) == ["https://app.lexisense.net", "http://localhost:3000"]


@pytest.mark.parametrize("raw", [None, "", "   ", ",,,"])
def test_empty_allow_list(raw):
    assert parse_cors_origins(raw) == []


@pytest.mark.parametrize(
    "raw",
    [
        "*",
        " * ",
        "https://*.example.com",
        "https://app.example.com,*",
        "*,https://app.example.com",
        "https://app.example.com, https://*.example.com",
        "http://localhost:3000\n,*",
    ],
)
def test_wildcard_origins_are_rejected(raw):
    with pytest.raises(CorsOriginError, match="wildcard"):
        parse_cors_origins(raw)


def test_load_strips_whitespace_and_rejects_a_wildcard(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " * \n")
    with pytest.raises(CorsOriginError, match="wildcard"):
        load_cors_origins()


def test_load_blank_is_an_empty_allow_list(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " \r\n ")
    assert load_cors_origins() == []


def test_error_does_not_echo_the_origin():
    secret_looking = "https://user:token@*.example.com"
    with pytest.raises(CorsOriginError) as caught:
        parse_cors_origins(secret_looking)
    assert "token" not in str(caught.value)
    assert "example.com" not in str(caught.value)


def _import_server(cors_origins: str) -> subprocess.CompletedProcess[str]:
    """Import ``server`` in a fresh interpreter so the startup check runs."""
    env = dict(os.environ)
    env["CORS_ORIGINS"] = cors_origins
    env.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production-32")
    env.setdefault("DATABASE_URL", "mongodb://localhost:27017/lexisense")
    env.setdefault("ENVIRONMENT", "test")
    return subprocess.run(
        [sys.executable, "-c", "import server"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )


def test_server_refuses_to_import_when_cors_origin_is_a_wildcard():
    out = _import_server("*")
    combined = out.stdout + out.stderr
    assert out.returncode != 0
    assert "CORS_ORIGINS rejects wildcard origins" in combined
    # The check runs before third-party imports, so a missing framework
    # package must not be what stopped the process.
    assert "ModuleNotFoundError" not in combined
    assert "No module named" not in combined
