"""The worker registers task modules and beat jobs only for enabled flags.

celery_app evaluates the flags at import time, so each case runs in a fresh
interpreter.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
FLAGS = ("AGENTIC_PHASE1_ENABLED", "AGENTIC_AGENTS_ENABLED", "AGENTIC_INTAKE_ENABLED", "AGENTIC_REDLINE_ENABLED")

PROBE = (
    "import json; from services.celery_app import celery_app as c, TASK_MODULES; "
    "c.loader.import_default_modules(); "
    "print(json.dumps({'modules': sorted(TASK_MODULES), 'beat': sorted(c.conf.beat_schedule), "
    "'tasks': sorted(t for t in c.tasks if t.startswith('services.') and not t.endswith('debug_task'))}))"
)


def _probe(**flags):
    env = {k: v for k, v in os.environ.items() if k not in FLAGS and k not in ("REDIS_URL", "CELERY_BROKER_URL")}
    env.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production")
    env.update(flags)
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=BACKEND, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_all_flags_off_registers_nothing():
    r = _probe()
    assert r == {"modules": [], "beat": [], "tasks": []}


def test_phase1_registers_only_risk_and_obligations():
    r = _probe(AGENTIC_PHASE1_ENABLED="true")
    assert r["modules"] == ["services.obligation_tasks", "services.risk_tasks"]
    assert r["beat"] == ["daily-obligation-alerts", "daily-risk-refresh", "weekly-cleanup"]
    assert r["tasks"] and all(t.split(".")[1] in ("risk_tasks", "obligation_tasks") for t in r["tasks"])


@pytest.mark.parametrize(
    "flag,module,beat",
    [
        ("AGENTIC_AGENTS_ENABLED", "services.agent_tasks", ["check-due-agents"]),
        ("AGENTIC_INTAKE_ENABLED", "services.intake_tasks", ["retry-failed-intakes"]),
        ("AGENTIC_REDLINE_ENABLED", "services.playbook_tasks", []),
    ],
)
def test_each_other_flag_is_isolated(flag, module, beat):
    r = _probe(**{flag: "true"})
    assert r["modules"] == [module]
    assert r["beat"] == beat
