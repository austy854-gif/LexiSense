"""CORS_ORIGINS must be exact http(s) origins, and cannot be empty in production."""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cors_origins import (
    CorsOriginError,
    load_cors_origins,
    parse_cors_origins,
    validate_cors_allow_list,
)

BACKEND = Path(__file__).resolve().parents[2]


def test_valid_http_https_and_port_origins_are_kept_and_trimmed():
    raw = (
        " https://app.lexisense.net , "
        "http://localhost:3000 , "
        "http://127.0.0.1:8080,"
    )
    assert parse_cors_origins(raw) == [
        "https://app.lexisense.net",
        "http://localhost:3000",
        "http://127.0.0.1:8080",
    ]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("http://example.com", ["http://example.com"]),
        ("https://example.com", ["https://example.com"]),
        ("http://localhost:3000", ["http://localhost:3000"]),
        ("https://app.example.com:8443", ["https://app.example.com:8443"]),
        ("https://[::1]:8080", ["https://[::1]:8080"]),
    ],
)
def test_valid_http_https_with_port(raw: str, expected: list[str]) -> None:
    assert parse_cors_origins(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", ",,,"])
def test_empty_allow_list(raw: str | None) -> None:
    assert parse_cors_origins(raw) == []


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://example.com",
        "javascript:alert(1)",
        "file:///tmp",
        "ws://example.com",
        "example.com",
        "//example.com",
    ],
)
def test_rejected_schemes_and_missing_scheme(raw: str) -> None:
    with pytest.raises(CorsOriginError, match="invalid origin"):
        parse_cors_origins(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/path",
        "https://example.com/",
        "http://localhost:3000/app",
        "https://example.com?q=1",
        "https://example.com?",
        "https://example.com#frag",
        "https://user:pass@example.com",
        "https://user@example.com",
        "https://example.com:99999",
        "https://example.com:",
        "https://",
    ],
)
def test_path_query_fragment_userinfo_and_malformed_are_rejected(
    raw: str,
) -> None:
    with pytest.raises(CorsOriginError, match="invalid origin"):
        parse_cors_origins(raw)


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
def test_wildcard_origins_are_rejected(raw: str) -> None:
    with pytest.raises(CorsOriginError, match="wildcard"):
        parse_cors_origins(raw)


def test_load_strips_whitespace_and_rejects_a_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", " * \n")
    monkeypatch.setenv("ENVIRONMENT", "test")
    with pytest.raises(CorsOriginError, match="wildcard"):
        load_cors_origins()


def test_load_blank_is_an_empty_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", " \r\n ")
    monkeypatch.setenv("ENVIRONMENT", "test")
    assert load_cors_origins() == []


def test_error_names_the_bad_value_but_redacts_userinfo() -> None:
    secret_looking = "https://user:token@example.com"
    with pytest.raises(CorsOriginError) as caught:
        parse_cors_origins(secret_looking)
    message = str(caught.value)
    assert "token" not in message
    assert "user:" not in message
    assert "example.com" in message
    assert "invalid origin" in message


def test_wildcard_error_names_the_bad_value() -> None:
    with pytest.raises(CorsOriginError) as caught:
        parse_cors_origins("https://*.example.com")
    message = str(caught.value)
    assert "https://*.example.com" in message
    assert "wildcard" in message


def test_empty_list_in_production_raises() -> None:
    with pytest.raises(CorsOriginError, match="ENVIRONMENT=production"):
        validate_cors_allow_list([], " production ")
    with pytest.raises(CorsOriginError, match="ENVIRONMENT=production"):
        validate_cors_allow_list([], "PRODUCTION")


def test_empty_list_in_development_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="cors_origins"):
        assert validate_cors_allow_list([], "development") == []
    assert "CORS_ORIGINS is empty" in caplog.text


def test_empty_list_in_test_and_unset_environment_only_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="cors_origins"):
        assert validate_cors_allow_list([], "test") == []
        assert validate_cors_allow_list([], None) == []
        assert validate_cors_allow_list([], "  ") == []
    assert caplog.text.count("CORS_ORIGINS is empty") == 3


def test_load_empty_in_production_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "  ")
    monkeypatch.setenv("ENVIRONMENT", " Production \n")
    with pytest.raises(CorsOriginError, match="ENVIRONMENT=production"):
        load_cors_origins()


def test_load_empty_in_development_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    with caplog.at_level(logging.WARNING, logger="cors_origins"):
        assert load_cors_origins() == []
    assert "CORS_ORIGINS is empty" in caplog.text


def test_non_empty_allow_list_is_unchanged_in_production() -> None:
    origins = ["https://app.lexisense.net"]
    assert validate_cors_allow_list(origins, "production") == origins


def _import_server(
    cors_origins: str, environment: str = "test"
) -> subprocess.CompletedProcess[str]:
    """Import ``server`` in a fresh interpreter so the startup check runs."""
    env = dict(os.environ)
    env["CORS_ORIGINS"] = cors_origins
    env["ENVIRONMENT"] = environment
    env.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production-32")
    env.setdefault("DATABASE_URL", "mongodb://localhost:27017/lexisense")
    return subprocess.run(
        [sys.executable, "-c", "import server"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )


def test_server_refuses_to_import_when_cors_origin_is_a_wildcard() -> None:
    out = _import_server("*")
    combined = out.stdout + out.stderr
    assert out.returncode != 0
    assert "CORS_ORIGINS rejects wildcard origin" in combined
    assert "'*'" in combined
    # The check runs before third-party imports, so a missing framework
    # package must not be what stopped the process.
    assert "ModuleNotFoundError" not in combined
    assert "No module named" not in combined
