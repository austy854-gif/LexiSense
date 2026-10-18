"""utils.env: environment values are stripped of surrounding whitespace.

Regression for the production outage where Railway's DATABASE_URL carried a
trailing newline: pymongo parsed ``w='majority\\n'`` (error 79
UnknownReplWriteConcern), index creation failed and /api/v1/ready stayed 503.
"""
import logging

import pytest
from pymongo.uri_parser import parse_uri

import utils.env as env_module
from utils.env import get_database_url, get_env

VAR = "LEXISENSE_TEST_ENV_VAR"
URI = (
    "mongodb://user:pw@cluster0-a.example.net:27017,cluster0-b.example.net:27017/lexisense"
    "?replicaSet=rs0&retryWrites=true&w=majority"
)


@pytest.fixture(autouse=True)
def _reset_whitespace_warnings(monkeypatch):
    """The DATABASE_URL warning is once-per-process; isolate each test."""
    monkeypatch.setattr(env_module, "_whitespace_warned", set())


@pytest.mark.parametrize(
    "raw",
    ["value\n", "value\r\n", "  value  ", "\tvalue\n\n", "\r\nvalue"],
    ids=["trailing-lf", "crlf", "spaces", "tab-and-lfs", "leading-crlf"],
)
def test_get_env_strips_surrounding_whitespace(monkeypatch, raw):
    monkeypatch.setenv(VAR, raw)
    assert get_env(VAR) == "value"


def test_get_env_clean_value_unchanged(monkeypatch):
    monkeypatch.setenv(VAR, "a b\tc")  # inner whitespace is preserved
    assert get_env(VAR) == "a b\tc"
    assert get_env(VAR, "fallback") == "a b\tc"


@pytest.mark.parametrize("raw", ["", "   ", "\n", "\r\n"])
def test_get_env_empty_returns_default(monkeypatch, raw):
    monkeypatch.setenv(VAR, raw)
    assert get_env(VAR) is None
    assert get_env(VAR, "fallback") == "fallback"


def test_get_env_unset_returns_default(monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    assert get_env(VAR) is None
    assert get_env(VAR, "fallback") == "fallback"


def test_database_url_trailing_newline_yields_clean_uri(monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", URI + "\n")
    monkeypatch.delenv("MONGO_URL", raising=False)
    with caplog.at_level(logging.WARNING, logger="utils.env"):
        url = get_database_url()
    assert url == URI
    # The write concern is the exact value that broke production.
    assert parse_uri(url, validate=False)["options"]["w"] == "majority"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "DATABASE_URL" in message
    assert "cluster0" not in message and "pw" not in message  # never log the value


def test_database_url_clean_value_no_warning(monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", URI)
    with caplog.at_level(logging.WARNING, logger="utils.env"):
        assert get_database_url() == URI
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_database_url_falls_back_to_mongo_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", " \n")
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017\r\n")
    assert get_database_url() == "mongodb://localhost:27017"


def test_database_url_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MONGO_URL", raising=False)
    assert get_database_url() is None


def test_raw_uri_with_newline_reproduces_the_outage():
    """Documents the root cause: unstripped, pymongo keeps the newline in ``w``."""
    assert parse_uri(URI + "\n", validate=False)["options"]["w"] == "majority\n"


def test_server_uses_clean_database_url(tmp_path):
    """Importing ``server`` with a newline-terminated DATABASE_URL yields a clean
    URI and a ``w='majority'`` client. Runs in a subprocess so the import's side
    effects (route ``init_db`` wiring, the Motor client) cannot leak."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    probe = (
        "import json, server; "
        "print(json.dumps({'url': server.mongo_url, "
        "'w': server.client.write_concern.document.get('w'), 'db': server.DB_NAME}))"
    )
    env = dict(os.environ)
    env.update({
        "DATABASE_URL": URI + "\n",
        "JWT_SECRET": env.get("JWT_SECRET") or "test-secret-do-not-use-in-production",
        "LOG_JSON": "false",
    })
    env.pop("DB_NAME", None)
    out = subprocess.run([sys.executable, "-c", probe], cwd=backend, env=env,
                         capture_output=True, text=True, check=True)
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result == {"url": URI, "w": "majority", "db": "lexisense"}
    assert "DATABASE_URL contained leading/trailing whitespace" in out.stdout + out.stderr


def test_database_url_warning_logged_once_per_process(monkeypatch, caplog):
    """Celery calls get_database_url() for every task; warn only the first time."""
    monkeypatch.setenv("DATABASE_URL", URI + "\n")
    with caplog.at_level(logging.WARNING, logger="utils.env"):
        for _ in range(5):
            assert get_database_url() == URI
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


@pytest.mark.parametrize("blank", ["", "   ", "\n", "\r\n", " \t\r\n "])
def test_whitespace_only_database_url_falls_back_to_mongo_url(monkeypatch, caplog, blank):
    monkeypatch.setenv("DATABASE_URL", blank)
    monkeypatch.setenv("MONGO_URL", "mongodb://mongo.internal:27017/lexisense")
    with caplog.at_level(logging.WARNING, logger="utils.env"):
        assert get_database_url() == "mongodb://mongo.internal:27017/lexisense"
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def _run_backend(probe, **overrides):
    """Run ``python -c probe`` in backend/ with env overrides (None = unset)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = dict(os.environ)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run([sys.executable, "-c", probe], cwd=Path(__file__).resolve().parents[2],
                          env=env, capture_output=True, text=True)


@pytest.mark.parametrize("environment", ["production\n", "production\r\n", "  PRODUCTION  ", "staging\n"])
def test_environment_with_whitespace_is_treated_as_production(environment):
    """A short JWT_SECRET must be refused when ENVIRONMENT is prod-like, even if
    the value carries a trailing newline (previously 'production\\n' slipped
    through as a non-production environment and only warned)."""
    out = _run_backend("import utils.auth", ENVIRONMENT=environment, JWT_SECRET="too-short")
    assert out.returncode != 0
    assert "JWT_SECRET must be at least 32 characters in" in out.stderr
    assert "too-short" not in out.stderr  # the secret is never echoed


def test_environment_development_short_secret_only_warns():
    out = _run_backend("import utils.auth; print('imported')", ENVIRONMENT="development\n", JWT_SECRET="too-short")
    assert out.returncode == 0, out.stderr
    assert "imported" in out.stdout


def test_server_imports_with_whitespace_in_log_settings():
    probe = "import logging, server; print('level=' + logging.getLevelName(logging.getLogger().level))"
    out = _run_backend(probe, LOG_LEVEL="INFO\n", LOG_JSON=" false\r\n", ENVIRONMENT="test\n",
                       FRONTEND_BUILD_DIR=None)
    assert out.returncode == 0, out.stderr
    assert "level=INFO" in out.stdout
