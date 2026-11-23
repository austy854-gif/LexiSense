"""The alert cron runs once, does not use Celery, and hides the healthcheck URL."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.run_alert_cron import (
    healthcheck_urls,
    lock_is_free,
    make_ping,
    run_once,
)

TOKEN = "healthcheck-token-do-not-log"


def test_healthcheck_urls():
    assert healthcheck_urls(None) == {}
    assert healthcheck_urls("  ") == {}
    assert healthcheck_urls(f"https://hc.example/{TOKEN}/") == {
        "start": f"https://hc.example/{TOKEN}/start",
        "success": f"https://hc.example/{TOKEN}",
        "fail": f"https://hc.example/{TOKEN}/fail",
    }


def test_lock_lease():
    now = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
    assert lock_is_free(None, now)
    assert lock_is_free("", now)
    assert lock_is_free((now - timedelta(seconds=1)).isoformat(), now)
    assert not lock_is_free((now + timedelta(minutes=5)).isoformat(), now)


def test_run_once_scans_and_pings_success():
    calls = []

    async def acquire():
        return "token-1"

    async def release(token):
        calls.append(("release", token))

    async def scan():
        calls.append("scan")

    code = asyncio.run(
        run_once(acquire=acquire, release=release, scan=scan, ping=calls.append)
    )
    assert code == 0
    assert calls == ["start", "scan", "success", ("release", "token-1")]


def test_overlapping_run_does_not_scan():
    scanned = []

    async def acquire():
        return None

    async def release(token):
        raise AssertionError(token)

    async def scan():
        scanned.append(1)

    code = asyncio.run(
        run_once(
            acquire=acquire,
            release=release,
            scan=scan,
            ping=lambda kind: scanned.append(kind),
        )
    )
    assert code == 0
    assert scanned == []


def test_scan_failure_pings_fail_and_releases():
    calls = []

    async def acquire():
        return "token-2"

    async def release(token):
        calls.append(("release", token))

    async def scan():
        raise RuntimeError("mail down")

    code = asyncio.run(
        run_once(acquire=acquire, release=release, scan=scan, ping=calls.append)
    )
    assert code == 1
    assert calls == ["start", "fail", ("release", "token-2")]


def test_ping_failure_does_not_log_the_url(caplog):
    ping = make_ping(f"http://127.0.0.1:1/{TOKEN}")
    with caplog.at_level(logging.WARNING):
        ping("start")
    assert TOKEN not in caplog.text


def test_cron_is_a_script_and_not_celery():
    source = (
        Path(__file__).resolve().parents[2] / "scripts" / "run_alert_cron.py"
    ).read_text(encoding="utf-8")
    assert "import celery" not in source.lower()
    assert "celery_app" not in source
    assert "HEALTHCHECKS_URL_CRON" in source
    assert "scan_expiration_alerts" in source
