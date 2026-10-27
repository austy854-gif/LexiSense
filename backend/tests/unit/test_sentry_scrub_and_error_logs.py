"""Sentry scrubbing, approval-email error logging, masked send results and
the opt-in legacy-invite expiry (script + startup flag)."""
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from scripts import expire_legacy_invites as script
from services import approval_tasks, email_service
from tests.unit.conftest import API_PREFIX, auth_headers, make_invitation
from utils import invite_expiry, sentry_scrub

BACKEND = Path(__file__).resolve().parents[2]

INVITE_TOKEN = "Zx9-invite_TOKEN_abcdefghijklmnopqrstuvwxyz0123"
APPROVAL_TOKEN = ("eyJhY3QiOiJhcHByb3ZlIiwiYWlkIjoiYS0xIiwiZXhwIjoxNzAwMDAwMDAwfQ"
                  ".q1w2e3r4t5y6u7i8o9p0a1s2d3f4g5h6j7k8l9z0x1c")
EMAIL = "jane.doe@client-corp.example"
ACCEPT_URL = f"https://app.lexisense.example/accept-invite?token={INVITE_TOKEN}&ref=mail"
UUID = "5f0c6a52-3a7e-4c1e-9d55-0b6f7b3c2a11"
EVENT_ID = "0123456789abcdef0123456789abcdef"
SECRETS = (INVITE_TOKEN, APPROVAL_TOKEN.split(".")[0], APPROVAL_TOKEN.split(".")[1], "jane.doe", "ref=mail")


def _assert_clean(blob):
    text = blob if isinstance(blob, str) else json.dumps(blob, default=str)
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} leaked"
    return text


# ---------------------------------------------------------------------------
# Sentry scrubber
# ---------------------------------------------------------------------------
class TestScrubString:
    def test_redacts_tokens_emails_and_query_values(self):
        out = sentry_scrub.scrub_string(f"send to {EMAIL} link {ACCEPT_URL} approve {APPROVAL_TOKEN}")
        _assert_clean(out)
        assert "***@client-corp.example" in out
        assert "https://app.lexisense.example/accept-invite?token=[REDACTED]&ref=[REDACTED]" in out

    def test_relative_url_query_redacted(self):
        assert sentry_scrub.scrub_string("/accept-invite?token=abc") == "/accept-invite?token=[REDACTED]"

    def test_ids_are_kept(self):
        text = f"contract {UUID} event {EVENT_ID}"
        assert sentry_scrub.scrub_string(text) == text

    def test_idempotent(self):
        once = sentry_scrub.scrub_string(f"{EMAIL} {ACCEPT_URL}")
        assert sentry_scrub.scrub_string(once) == once


def _full_event():
    return {
        "event_id": EVENT_ID,
        "level": "error",
        "release": "2.0.0",
        "message": f"Failed for {EMAIL}",
        "logentry": {"message": "Failed %s %s", "params": [EMAIL, ACCEPT_URL]},
        "exception": {"values": [{
            "type": "RuntimeError",
            "value": f"rejected {EMAIL}: {ACCEPT_URL}",
            "stacktrace": {"frames": [{"function": "send", "lineno": 3,
                                       "vars": {"html": f"<a href='{ACCEPT_URL}'>", "token": INVITE_TOKEN}}]},
        }]},
        "request": {
            "url": ACCEPT_URL,
            "method": "POST",
            "query_string": f"token={INVITE_TOKEN}&ref=mail",
            "headers": {"Authorization": f"Bearer {APPROVAL_TOKEN}", "Cookie": "session=abc",
                        "Referer": ACCEPT_URL, "User-Agent": "pytest"},
            "cookies": {"session": "abc"},
            "data": {"token": INVITE_TOKEN, "password": "hunter22", "email": EMAIL, "note": ACCEPT_URL},
            "env": {"REMOTE_ADDR": "10.0.0.1"},
        },
        "extra": {"accept_url": ACCEPT_URL, "params": {"to": [EMAIL], "html": "<p>secret</p>"}, "id": UUID},
        "contexts": {"trace": {"trace_id": EVENT_ID}, "email": {"subject": f"Invite for {EMAIL}"}},
        "tags": {"recipient": EMAIL},
        "user": {"id": "u-1", "email": EMAIL, "ip_address": "1.2.3.4"},
        "breadcrumbs": {"values": [
            {"category": "httplib", "data": {"url": ACCEPT_URL, "http.query": f"token={INVITE_TOKEN}"}},
            {"category": "log", "message": f"Would send invitation to {EMAIL} with data: {{'token': '{INVITE_TOKEN}'}}"},
        ]},
    }


class TestBeforeSend:
    def test_scrubs_every_section(self):
        out = sentry_scrub.before_send(_full_event(), {})
        text = _assert_clean(out)
        assert "hunter22" not in text and "<p>secret</p>" not in text and "session=abc" not in text
        assert "1.2.3.4" not in text and "10.0.0.1" not in text
        assert out["event_id"] == EVENT_ID and out["contexts"]["trace"]["trace_id"] == EVENT_ID
        assert out["extra"]["id"] == UUID
        assert out["user"] == {"id": "u-1"}
        assert out["request"]["url"] == "https://app.lexisense.example/accept-invite"
        assert out["request"]["headers"]["Authorization"] == "[REDACTED]"
        assert out["request"]["headers"]["User-Agent"] == "pytest"
        assert out["request"]["cookies"] == "[REDACTED]"
        assert out["request"]["query_string"] == "token=[REDACTED]&ref=[REDACTED]"
        assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert out["exception"]["values"][0]["type"] == "RuntimeError"
        assert "***@client-corp.example" in out["exception"]["values"][0]["value"]

    def test_never_raises_and_falls_back_to_minimal_event(self, monkeypatch, caplog):
        def broken(event):
            raise TypeError("boom")
        monkeypatch.setattr(sentry_scrub, "scrub_event", broken)
        caplog.set_level(logging.WARNING, logger=sentry_scrub.logger.name)

        out = sentry_scrub.before_send(_full_event(), {})

        _assert_clean(out)
        assert out["event_id"] == EVENT_ID
        assert out["exception"]["values"] == [{"type": "RuntimeError", "value": "[REDACTED]"}]
        assert "scrubber failed" in out["message"]
        assert any("scrubber failed (TypeError)" in r.getMessage() for r in caplog.records)

    def test_drops_with_log_when_even_fallback_fails(self, monkeypatch, caplog):
        monkeypatch.setattr(sentry_scrub, "scrub_event", lambda e: 1 / 0)
        monkeypatch.setattr(sentry_scrub, "_minimal_event", lambda e: 1 / 0)
        caplog.set_level(logging.WARNING, logger=sentry_scrub.logger.name)
        assert sentry_scrub.before_send(_full_event(), {}) is None
        assert any("event dropped" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("event", [{}, {"exception": None, "request": None}, {"request": {"headers": [["a", EMAIL]]}},
                                       {"exception": {"values": [None]}}, {"extra": {"deep": [[[[EMAIL]]]]}}])
    def test_odd_shapes(self, event):
        _assert_clean(sentry_scrub.before_send(event, None))

    def test_deeply_nested_is_bounded(self):
        nested = EMAIL
        for _ in range(100):
            nested = {"x": nested}
        _assert_clean(sentry_scrub.before_send({"extra": nested}, None))


class TestBeforeBreadcrumb:
    def test_http_breadcrumb(self):
        crumb = {"type": "http", "category": "httplib",
                 "data": {"url": ACCEPT_URL, "http.query": f"token={INVITE_TOKEN}", "method": "GET"}}
        out = sentry_scrub.before_breadcrumb(crumb, {})
        _assert_clean(out)
        assert out["data"]["url"] == "https://app.lexisense.example/accept-invite"
        assert out["data"]["method"] == "GET"

    def test_log_breadcrumb(self):
        out = sentry_scrub.before_breadcrumb({"category": "x", "message": f"to {EMAIL} {ACCEPT_URL}"}, {})
        _assert_clean(out)

    def test_failure_is_logged_not_raised(self, monkeypatch, caplog):
        monkeypatch.setattr(sentry_scrub, "scrub", lambda *a, **k: 1 / 0)
        caplog.set_level(logging.WARNING, logger=sentry_scrub.logger.name)
        assert sentry_scrub.before_breadcrumb({"message": EMAIL}, {}) is None
        assert any("breadcrumb scrubber failed" in r.getMessage() for r in caplog.records)


class _CaptureTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


def test_real_sdk_event_has_no_locals_or_secrets():
    transport = _CaptureTransport()
    client = sentry_sdk.Client(dsn="https://public@sentry.invalid/1", transport=transport,
                               **sentry_scrub.sentry_init_options())

    def send(params):
        html = f"<a href='{ACCEPT_URL}'>accept</a>"  # noqa: F841 - must not be captured
        raise RuntimeError(f"rejected {params['to']} {ACCEPT_URL}")

    with sentry_sdk.isolation_scope() as scope:
        scope.set_client(client)
        scope.add_breadcrumb(message=f"Would send to {EMAIL}", category="log")
        scope.set_extra("token", INVITE_TOKEN)
        try:
            send({"to": EMAIL})
        except RuntimeError as exc:
            scope.capture_exception(exc)
    client.flush(timeout=2)

    payloads = [item.payload.json for env in transport.envelopes for item in env.items
                if item.headers.get("type") == "event"]
    assert payloads
    text = _assert_clean(payloads)
    assert '"vars"' not in text
    assert "RuntimeError" in text and "***@client-corp.example" in text


def test_server_wires_scrubber_into_sentry_init():
    probe = """
import json, sentry_sdk
seen = {}
sentry_sdk.init = lambda **kw: seen.update(kw)
import server
from utils import sentry_scrub
print(json.dumps({"locals": seen.get("include_local_variables"),
    "pii": seen.get("send_default_pii", "unset"), "data_collection": seen.get("data_collection", "unset"),
    "before_send": seen.get("before_send") is sentry_scrub.before_send,
    "before_breadcrumb": seen.get("before_breadcrumb") is sentry_scrub.before_breadcrumb,
    "before_send_transaction": seen.get("before_send_transaction") is sentry_scrub.before_send}))
"""
    env = dict(os.environ, SENTRY_DSN="https://public@sentry.invalid/1",
               JWT_SECRET=os.environ.get("JWT_SECRET", "test-secret"), MONGO_URL="mongodb://localhost:27017")
    out = subprocess.run([sys.executable, "-c", probe], cwd=BACKEND, env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {
        # Neither PII option is passed: the SDK default is "no default PII" (see
        # utils/sentry_scrub.sentry_init_options and test_sentry_data_collection).
        "locals": False, "pii": "unset", "data_collection": "unset",
        "before_send": True, "before_breadcrumb": True, "before_send_transaction": True,
    }


# ---------------------------------------------------------------------------
# approval_tasks error logging
# ---------------------------------------------------------------------------
@pytest.fixture
async def failing_notification(db, monkeypatch):
    async def fake_render(_db, _notification):
        return f"Approve for {EMAIL}", f"<a href='https://app.example/approve?token={APPROVAL_TOKEN}'>"

    async def failing_send(to, subject, html, **kwargs):
        raise RuntimeError(f"provider rejected {to}: {subject} {html}")

    monkeypatch.setattr(approval_tasks, "_render", fake_render)
    monkeypatch.setattr(email_service, "send_raw_email", failing_send)
    await db.email_notifications.insert_one({"id": "n-1", "organizationId": "org-1", "to": EMAIL,
                                             "kind": "approval_request", "status": "queued"})
    return db


def _log_text(caplog):
    return "\n".join(f"{r.getMessage()} {r.exc_text or ''} {r.exc_info or ''}" for r in caplog.records)


class TestApprovalTaskLogging:
    async def test_deliver_logs_metadata_only(self, failing_notification, caplog):
        caplog.set_level(logging.DEBUG)
        with pytest.raises(RuntimeError):
            await approval_tasks.deliver_notification(failing_notification, "n-1", "org-1")
        text = _assert_clean(_log_text(caplog))
        assert "template=approval_request" in text
        assert "recipient_domain=client-corp.example" in text
        assert "error_type=RuntimeError" in text
        assert "provider rejected" not in text

    def test_celery_task_logs_type_and_retries_with_sanitised_exception(self, failing_notification, monkeypatch, caplog):
        monkeypatch.setattr(approval_tasks, "_db", lambda db=None: failing_notification)
        caplog.set_level(logging.DEBUG)
        with pytest.raises(approval_tasks.ApprovalEmailDeliveryError) as raised:
            approval_tasks.send_approval_email_task.run("n-1", "org-1")
        assert str(raised.value) == "RuntimeError"
        text = _assert_clean(_log_text(caplog))
        assert "provider rejected" not in text
        assert "error_type=RuntimeError" in text

    async def test_inline_dispatch_logs_no_exception_text(self, failing_notification, monkeypatch, caplog):
        monkeypatch.setattr("services.celery_app.CELERY_BROKER_URL", None, raising=False)
        caplog.set_level(logging.DEBUG)
        assert await approval_tasks.dispatch_notification(failing_notification, "n-1", "org-1") == "failed"
        text = _assert_clean(_log_text(caplog))
        assert "provider rejected" not in text and "Traceback" not in text


class TestStoredLastError:
    async def test_resend_error_stores_type_and_status_only(self, failing_notification, monkeypatch):
        from resend.exceptions import ValidationError

        async def provider_reject(to, subject, html, **kwargs):
            raise ValidationError(f"Invalid `to` field: {to}. Body: {html}", "validation_error", 422)

        monkeypatch.setattr(email_service, "send_raw_email", provider_reject)
        with pytest.raises(ValidationError):
            await approval_tasks.deliver_notification(failing_notification, "n-1", "org-1", final_attempt=True)

        doc = await failing_notification.email_notifications.find_one({"id": "n-1"}, {"_id": 0})
        assert doc["status"] == "failed"
        assert doc["lastError"] == "ValidationError (HTTP 422)"
        # Only the intended ``to`` field may hold the address.
        stored = {k: v for k, v in doc.items() if k != "to"}
        text = _assert_clean(stored)
        assert "Invalid" not in text and "validation_error" not in text and "client-corp" not in text

    async def test_generic_error_stores_type_only(self, failing_notification):
        with pytest.raises(RuntimeError):
            await approval_tasks.deliver_notification(failing_notification, "n-1", "org-1", final_attempt=False)
        doc = await failing_notification.email_notifications.find_one({"id": "n-1"}, {"_id": 0})
        assert doc["status"] == "retrying" and doc["lastError"] == "RuntimeError"
        stored = {k: v for k, v in doc.items() if k != "to"}
        _assert_clean(stored)
        assert "provider rejected" not in json.dumps(stored) and "client-corp" not in json.dumps(stored)

    @pytest.mark.parametrize("attrs, expected", [
        ({"code": 429}, "Boom (HTTP 429)"),
        ({"code": "503"}, "Boom (HTTP 503)"),
        ({"status_code": 500}, "Boom (HTTP 500)"),
        ({"response": type("R", (), {"status_code": 401})()}, "Boom (HTTP 401)"),
        ({"code": "ECONNRESET jane.doe@client-corp.example"}, "Boom"),
        ({"code": True}, "Boom"),
        ({"code": 42}, "Boom"),
        ({}, "Boom"),
    ])
    def test_summary(self, attrs, expected):
        exc = type("Boom", (Exception,), {})(f"text {EMAIL}")
        for key, value in attrs.items():
            setattr(exc, key, value)
        assert approval_tasks.safe_error_summary(exc) == expected


# ---------------------------------------------------------------------------
# send_email return value
# ---------------------------------------------------------------------------
async def test_send_email_result_masks_recipient(monkeypatch):
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(email_service.resend.Emails, "send", lambda params, *a: {"id": "msg_1"})
    result = await email_service.send_email(EMAIL, "expiration_alert", {"contract_id": UUID})
    assert result == {"status": "success", "message": "Email sent to ***@client-corp.example", "email_id": "msg_1"}
    _assert_clean(result)


# ---------------------------------------------------------------------------
# Legacy invite expiry
# ---------------------------------------------------------------------------
CUTOFF = datetime(2026, 10, 18, 7, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat()


@pytest.fixture
async def invite_db(db):
    old = CUTOFF - timedelta(days=2)
    new = CUTOFF + timedelta(hours=1)
    docs = [
        make_invitation("org-1", "admin", token=INVITE_TOKEN, legacy_plaintext=True, email="old1@x.example"),
        make_invitation("org-1", "admin", token="t-old2", email="old2@x.example"),
        make_invitation("org-1", "admin", token="t-new", email="new@x.example"),
        make_invitation("org-1", "admin", token="t-acc", status="accepted", email="acc@x.example"),
        make_invitation("org-1", "admin", token="t-bad", email="bad@x.example"),
    ]
    docs[0]["createdAt"] = _iso(old)
    docs[1]["createdAt"] = _iso(old).replace("+00:00", "Z")
    docs[2]["createdAt"] = _iso(new)
    docs[3]["createdAt"] = _iso(old)
    docs[4]["createdAt"] = "not-a-date"
    for doc in docs:
        await db.invitations.insert_one(dict(doc))
    return db


async def _statuses(db):
    return {d["email"]: d["status"] async for d in db.invitations.find({}, {"_id": 0})}


async def _record_migration(db, when=CUTOFF):
    await db.schema_migrations.insert_one({"version": 3, "name": "hash_invitation_tokens", "appliedAt": _iso(when)})


class TestExpireLegacyInvites:
    def test_dry_run_is_the_default(self):
        assert script.build_parser().parse_args([]).apply is False

    async def test_dry_run_counts_only_and_writes_nothing(self, invite_db):
        await _record_migration(invite_db)
        before = await _statuses(invite_db)
        lines = []
        assert await script.run(invite_db, out=lines.append) == 0
        assert await _statuses(invite_db) == before
        output = "\n".join(lines)
        assert "DRY RUN" in output and "pending total:   4" in output
        assert "created before:  3 (incl. 1 with unparseable createdAt)" in output and "expired now:     0" in output
        assert "@" not in output and INVITE_TOKEN not in output and "t-old" not in output

    async def test_apply_expires_only_old_pending_and_is_idempotent(self, invite_db):
        await _record_migration(invite_db)
        lines = []
        assert await script.run(invite_db, apply=True, out=lines.append) == 0
        assert "expired now:     3" in lines[-1]
        assert await _statuses(invite_db) == {
            "old1@x.example": "expired", "old2@x.example": "expired", "bad@x.example": "expired",
            "new@x.example": "pending", "acc@x.example": "accepted",
        }
        doc = await invite_db.invitations.find_one({"email": "old2@x.example"})
        assert doc["expiredReason"] == invite_expiry.EXPIRY_REASON and doc["expiresAt"] == doc["expiredAt"]
        lines.clear()
        await script.run(invite_db, apply=True, out=lines.append)
        assert "expired now:     0" in lines[-1]

    async def test_explicit_before(self, invite_db):
        report = await invite_expiry.expire_pending_invites_before(
            invite_db, invite_expiry.parse_iso("2026-10-01T00:00:00Z"), apply=True)
        assert report.expired == 1  # only the unparseable one is older than an early cutoff
        lines = []
        assert await script.run(invite_db, before="2030-01-01T00:00:00", apply=True, out=lines.append) == 0
        assert (await _statuses(invite_db))["new@x.example"] == "expired"

    @pytest.mark.parametrize("kwargs", [{"before": "yesterday"}, {}])
    async def test_refuses_without_valid_cutoff(self, invite_db, kwargs):
        lines = []
        assert await script.run(invite_db, apply=True, out=lines.append, **kwargs) == 2
        assert "error" in lines[0]
        assert "expired" not in (await _statuses(invite_db)).values()

    async def test_expired_invite_can_be_reissued_and_old_link_fails(self, client, db, users):
        legacy = make_invitation(users["admin"]["organizationId"], users["admin"]["id"], token=INVITE_TOKEN,
                                 legacy_plaintext=True, email="old1@x.example")
        legacy["createdAt"] = _iso(CUTOFF - timedelta(days=1))
        await db.invitations.insert_one(dict(legacy))
        await _record_migration(db)
        await script.run(db, apply=True, out=lambda *_: None)

        accept = await client.post(f"{API_PREFIX}/team/accept-invite",
                                   json={"token": INVITE_TOKEN, "password": "Sup3rSecret!"})
        assert accept.status_code == 404
        reinvite = await client.post(f"{API_PREFIX}/team/invite", json={"email": "old1@x.example"},
                                     headers=auth_headers(users["admin"]))
        assert reinvite.status_code == 200


class TestStartupFlag:
    async def test_off_by_default(self, invite_db, monkeypatch):
        monkeypatch.delenv(invite_expiry.STARTUP_FLAG, raising=False)
        await _record_migration(invite_db)
        before = await _statuses(invite_db)
        assert await invite_expiry.maybe_expire_on_startup(invite_db) is None
        assert await _statuses(invite_db) == before

    async def test_on_uses_migration_cutoff(self, invite_db, monkeypatch):
        monkeypatch.setenv(invite_expiry.STARTUP_FLAG, "true")
        await _record_migration(invite_db)
        report = await invite_expiry.maybe_expire_on_startup(invite_db)
        assert report.expired == 3 and (await _statuses(invite_db))["new@x.example"] == "pending"
        assert (await invite_expiry.maybe_expire_on_startup(invite_db)).expired == 0

    async def test_on_without_migration_skips(self, invite_db, monkeypatch, caplog):
        monkeypatch.setenv(invite_expiry.STARTUP_FLAG, "1")
        assert await invite_expiry.maybe_expire_on_startup(invite_db) is None
        assert "expired" not in (await _statuses(invite_db)).values()
