"""The forbidden-route check in the repo-root ``backend_test.py`` smoke suite.

``/agentic`` is forbidden only while every agentic feature flag is off, read
through ``utils.feature_flags.enabled_groups`` -- the same helper server.py
uses to decide whether to mount the agentic router. ``/auth/2fa``, ``/tags`` and
``/reports`` stay forbidden whatever the flags.
"""
import importlib.util
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI

from routes.agentic import build_router
from utils.feature_flags import ROUTE_GROUP_FLAGS, enabled_groups

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTIC_FLAGS = sorted(set(ROUTE_GROUP_FLAGS.values()))
OTHER_FORBIDDEN_PATHS = [
    "/api/v1/auth/2fa/setup",
    "/api/v1/tags",
    "/api/v1/tags/{tag_id}",
    "/api/v1/reports/summary",
]
ALLOWED_PATHS = ["/api/v1/", "/api/v1/auth/login", "/api/v1/contracts/{contract_id}"]


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("backend_test", REPO_ROOT / "backend_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def flags_off(monkeypatch):
    for flag in AGENTIC_FLAGS:
        monkeypatch.delenv(flag, raising=False)


@pytest.fixture(scope="module")
def agentic_paths():
    """OpenAPI paths of the real agentic router with every group mounted."""
    app = FastAPI()
    api = APIRouter(prefix="/api/v1")
    api.include_router(build_router(set(ROUTE_GROUP_FLAGS)))
    app.include_router(api)
    paths = list(app.openapi()["paths"])
    assert paths and all(p.startswith("/api/v1/agentic/") for p in paths)
    return paths


def test_flags_off_forbids_every_agentic_path(smoke, flags_off, agentic_paths):
    assert enabled_groups() == set()  # server.py would not mount /agentic
    patterns = smoke.forbidden_route_patterns()
    assert "/agentic" in patterns
    assert smoke.find_forbidden_paths(agentic_paths, patterns) == agentic_paths
    assert smoke.find_forbidden_paths(ALLOWED_PATHS, patterns) == []


@pytest.mark.parametrize("value", ["false", "0", "no", "off", ""])
def test_falsy_flag_values_count_as_off(smoke, flags_off, monkeypatch, agentic_paths, value):
    for flag in AGENTIC_FLAGS:
        monkeypatch.setenv(flag, value)
    patterns = smoke.forbidden_route_patterns()
    assert "/agentic" in patterns
    assert smoke.find_forbidden_paths(agentic_paths, patterns) == agentic_paths


@pytest.mark.parametrize("flag", AGENTIC_FLAGS)
def test_any_flag_on_allows_agentic_but_keeps_other_patterns(smoke, flags_off, monkeypatch, agentic_paths, flag):
    monkeypatch.setenv(flag, "true")
    assert enabled_groups()  # server.py would mount /agentic
    patterns = smoke.forbidden_route_patterns()
    assert "/agentic" not in patterns
    assert smoke.find_forbidden_paths(agentic_paths, patterns) == []
    assert smoke.find_forbidden_paths(OTHER_FORBIDDEN_PATHS, patterns) == OTHER_FORBIDDEN_PATHS
    assert smoke.find_forbidden_paths(ALLOWED_PATHS, patterns) == []


@pytest.mark.parametrize("flags_on", [False, True])
def test_other_patterns_are_unconditional(smoke, flags_off, monkeypatch, flags_on):
    if flags_on:
        for flag in AGENTIC_FLAGS:
            monkeypatch.setenv(flag, "1")
    patterns = smoke.forbidden_route_patterns()
    for pattern in smoke.ALWAYS_FORBIDDEN_PATTERNS:
        assert pattern in patterns
    assert smoke.find_forbidden_paths(OTHER_FORBIDDEN_PATHS, patterns) == OTHER_FORBIDDEN_PATHS


def test_mixed_spec_reports_each_offending_path_once(smoke, flags_off, agentic_paths):
    paths = ALLOWED_PATHS + OTHER_FORBIDDEN_PATHS + agentic_paths[:1]
    found = smoke.find_forbidden_paths(paths, smoke.forbidden_route_patterns())
    assert found == OTHER_FORBIDDEN_PATHS + agentic_paths[:1]


@pytest.mark.parametrize("state", [set(), {"AGENTIC_PHASE1_ENABLED"}, set(AGENTIC_FLAGS)])
def test_agrees_with_server_mount_decision(smoke, state):
    """Explicit env_flag: /agentic forbidden <=> enabled_groups() is empty."""
    env_flag = state.__contains__
    mounted = bool(enabled_groups(env_flag))
    assert ("/agentic" in smoke.forbidden_route_patterns(env_flag)) is (not mounted)
