import pytest
from datetime import datetime, timezone, timedelta
from services.agent_tasks import cron_to_next_run

def test_cron_to_next_run_daily():
    """Test cron_to_next_run for daily at 9 AM."""
    now = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)
    next_run = cron_to_next_run("0 9 * * *", now)
    assert next_run.hour == 9
    assert next_run.minute == 0
    # Should be tomorrow since 10 AM > 9 AM
    assert next_run.day == 14

def test_cron_to_next_run_daily_before_time():
    """Test cron_to_next_run when current time is before scheduled time."""
    now = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)
    next_run = cron_to_next_run("0 9 * * *", now)
    assert next_run.hour == 9
    assert next_run.day == 13  # Today

def test_cron_to_next_run_invalid_format():
    """Test cron_to_next_run with invalid format defaults to 24 hours."""
    now = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)
    next_run = cron_to_next_run("invalid", now)
    assert next_run == now + timedelta(hours=24)

def test_cron_to_next_run_every_6_hours():
    """Test cron_to_next_run for every 6 hours (not fully implemented, falls back)."""
    now = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)
    next_run = cron_to_next_run("0 */6 * * *", now)
    # Falls back to daily
    assert next_run == now + timedelta(days=1)