"""Invite tokens must never reach logs, API responses or the database in clear.

* ``email_service`` logs metadata only (template, recipient domain, provider
  message id) on the missing-key, success and error paths.
* Invitations store ``sha256(token)`` in ``tokenHash``; accept looks up by
  hash, and legacy plaintext records still work and are migrated.
"""
import hashlib
import json
import logging

import pytest

from routes import team as team_route
from services import email_service
from tests.unit.conftest import (
    API_PREFIX,
    auth_headers,
    iso_in,
    make_invitation,
)
from utils import invite_tokens
from utils.database import INDEX_SPECIFICATION, apply_indexes
from utils.migrations import MIGRATIONS

ACCEPT_URL = f"{API_PREFIX}/team/accept-invite"
INVITE_URL = f"{API_PREFIX}/team/invite"
LIST_URL = f"{API_PREFIX}/team/invitations"
PASSWORD = "Sup3rSecret!"

RAW_TOKEN = "RAW-invite-token-0123456789abcdef"
RECIPIENT = "new.person@client-corp.example"
APP_URL = "https://app.lexisense.example"

# Keep a handle on the real sender: the ``app`` fixture stubs it out.
_REAL_SEND_INVITATION_EMAIL = email_service.send_invitation_email


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _all_log_text(caplog):
    """Every rendered message plus every record attribute (extras, args).

    The test HTTP client's own request log (``httpx``) is excluded: it records
    the test URL, not application output.
    """
    chunks = []
    for record in caplog.records:
        if record.name.startswith(("httpx", "httpcore")):
            continue
        chunks.append(record.getMessage())
        chunks.append(repr(record.args))
        chunks.append(repr({k: v for k, v in record.__dict__.items() if k != "args"}))
    return "\n".join(chunks)


def _assert_no_secrets(caplog, *secrets):
    text = _all_log_text(caplog)
    for secret in secrets:
        assert secret not in text, f"{secret!r} leaked into logs"
    assert "accept-invite" not in text
    assert "http" not in text
    assert "<html" not in text.lower()


async def _invite(org_name="Acme Legal", **kwargs):
    return await _REAL_SEND_INVITATION_EMAIL(
        to_email=RECIPIENT,
        inviter_name="Ada Admin",
        organization_name=org_name,
        role="user",
        token=RAW_TOKEN,
        **kwargs,
    )


@pytest.fixture
def app_url(monkeypatch):
    monkeypatch.setattr(email_service, "APP_URL", APP_URL)


# ---------------------------------------------------------------------------
# email_service logging
# ---------------------------------------------------------------------------
class TestEmailLogRedaction:
    async def test_missing_key_logs_warning_with_metadata_only(self, monkeypatch, caplog, app_url):
        monkeypatch.setattr(email_service, "RESEND_API_KEY", None)
        caplog.set_level(logging.DEBUG)

        result = await _invite(org_name="Secret Merger Co")

        assert result["status"] == "logged"
        _assert_no_secrets(caplog, RAW_TOKEN, RECIPIENT, "new.person", "Secret Merger Co", "Ada Admin")
        warnings = [r for r in caplog.records if r.name == email_service.logger.name]
        assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
        message = warnings[0].getMessage()
        assert "template=invitation" in message
        assert "recipient_domain=client-corp.example" in message

    async def test_success_logs_message_id_only(self, monkeypatch, caplog, app_url):
        monkeypatch.setattr(email_service, "RESEND_API_KEY", "re_test_key")
        sent = {}

        def fake_send(params, *args):
            sent.update(params)
            return {"id": "msg_abc123"}

        monkeypatch.setattr(email_service.resend.Emails, "send", fake_send)
        caplog.set_level(logging.DEBUG)

        result = await _invite()

        assert result["status"] == "success" and result["email_id"] == "msg_abc123"
        # The raw token goes to the recipient (and only there).
        assert RAW_TOKEN in sent["html"]
        _assert_no_secrets(caplog, RAW_TOKEN, RECIPIENT, sent["subject"])
        message = "\n".join(r.getMessage() for r in caplog.records)
        assert "message_id=msg_abc123" in message
        assert "recipient_domain=client-corp.example" in message
        assert "template=invitation" in message

    async def test_provider_error_does_not_echo_payload(self, monkeypatch, caplog, app_url):
        monkeypatch.setattr(email_service, "RESEND_API_KEY", "re_test_key")

        def failing_send(params, *args):
            # Providers commonly echo the request back in the error text.
            raise RuntimeError(f"rejected {params['to']} {params['html']}")

        monkeypatch.setattr(email_service.resend.Emails, "send", failing_send)
        caplog.set_level(logging.DEBUG)

        result = await _invite()

        assert result["status"] == "error"
        assert RAW_TOKEN not in json.dumps(result) and RECIPIENT not in json.dumps(result)
        _assert_no_secrets(caplog, RAW_TOKEN, RECIPIENT)
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors and "error_type=RuntimeError" in errors[0].getMessage()
        assert "recipient_domain=client-corp.example" in errors[0].getMessage()

    async def test_raw_email_missing_key_does_not_log_subject_or_address(self, monkeypatch, caplog):
        monkeypatch.setattr(email_service, "RESEND_API_KEY", None)
        caplog.set_level(logging.DEBUG)
        subject = f"Approve: {APP_URL}/approve?token={RAW_TOKEN}"

        result = await email_service.send_raw_email(RECIPIENT, subject, f"<html>{subject}</html>")

        assert result["status"] == "logged"
        _assert_no_secrets(caplog, RAW_TOKEN, RECIPIENT, subject)
        assert "recipient_domain=client-corp.example" in caplog.records[-1].getMessage()

    @pytest.mark.parametrize(
        "address, expected",
        [("a@B.Example", "b.example"), ("no-at-sign", "unknown"), ("", "unknown"), (None, "unknown"),
         ("x@", "unknown"), ('"a@b"@c.example', "c.example")],
    )
    def test_recipient_domain(self, address, expected):
        assert email_service._recipient_domain(address) == expected


# ---------------------------------------------------------------------------
# Hashed storage via the real invite endpoint
# ---------------------------------------------------------------------------
@pytest.fixture
def captured_tokens(monkeypatch):
    """Route invites through the real email_service (missing-key path) while
    capturing the raw token the email would carry."""
    tokens = []

    async def capture(**kwargs):
        tokens.append(kwargs["token"])
        return await _REAL_SEND_INVITATION_EMAIL(**kwargs)

    monkeypatch.setattr(team_route, "send_invitation_email", capture)
    monkeypatch.setattr(email_service, "RESEND_API_KEY", None)
    monkeypatch.setattr(email_service, "APP_URL", APP_URL)
    return tokens


class TestHashedStorage:
    async def test_invite_stores_only_hash_and_never_exposes_token(
        self, client, db, users, captured_tokens, caplog
    ):
        caplog.set_level(logging.DEBUG)
        headers = auth_headers(users["admin"])

        response = await client.post(INVITE_URL, json={"email": RECIPIENT, "role": "user"}, headers=headers)
        assert response.status_code == 200
        (raw,) = captured_tokens
        assert raw and len(raw) >= 32

        doc = await db.invitations.find_one({"email": RECIPIENT}, {"_id": 0})
        assert "token" not in doc
        assert doc["tokenHash"] == _sha(raw) == invite_tokens.hash_invite_token(raw)
        assert raw not in json.dumps(doc)

        listed = await client.get(LIST_URL, headers=headers)
        for body in (response.text, listed.text):
            assert raw not in body
            assert doc["tokenHash"] not in body
            assert "token" not in body.lower()

        _assert_no_secrets(caplog, raw, RECIPIENT)

        accepted = await client.post(ACCEPT_URL, json={"token": raw, "password": PASSWORD})
        assert accepted.status_code == 200
        assert (await db.invitations.find_one({"id": doc["id"]}))["status"] == "accepted"
        assert await db.users.find_one({"email": RECIPIENT})

    async def test_hash_itself_is_not_accepted_as_token(self, client, db, users, captured_tokens):
        await client.post(INVITE_URL, json={"email": RECIPIENT}, headers=auth_headers(users["admin"]))
        doc = await db.invitations.find_one({"email": RECIPIENT})
        response = await client.post(ACCEPT_URL, json={"token": doc["tokenHash"], "password": PASSWORD})
        assert response.status_code == 404

    async def test_two_invites_coexist_under_index_spec(self, db, users, client, captured_tokens):
        await apply_indexes(db)
        headers = auth_headers(users["admin"])
        for email in ("one@x.example", "two@y.example"):
            r = await client.post(INVITE_URL, json={"email": email}, headers=headers)
            assert r.status_code == 200
        assert len(set(captured_tokens)) == 2

    def test_index_on_token_hash_and_none_on_plaintext(self):
        specs = INDEX_SPECIFICATION["invitations"]
        hashed = [s for s in specs if s["keys"] == [("tokenHash", 1)]]
        assert hashed and hashed[0]["unique"] and hashed[0]["sparse"]
        assert not [s for s in specs if s["keys"] == [("token", 1)]]


# ---------------------------------------------------------------------------
# Accept: hash lookup, legacy fallback, rejection
# ---------------------------------------------------------------------------
class TestAcceptByHash:
    async def test_accept_with_raw_token(self, client, db, org_id, users):
        inv = make_invitation(org_id, invited_by=users["admin"]["id"], token=RAW_TOKEN)
        assert "token" not in inv and inv["tokenHash"] == _sha(RAW_TOKEN)
        await db.invitations.insert_one(dict(inv))

        r = await client.post(ACCEPT_URL, json={"token": RAW_TOKEN, "password": PASSWORD})
        assert r.status_code == 200
        assert RAW_TOKEN not in r.text and inv["tokenHash"] not in r.text

    async def test_legacy_plaintext_token_works_and_is_migrated(self, client, db, org_id, users):
        inv = make_invitation(org_id, invited_by=users["admin"]["id"], token=RAW_TOKEN, legacy_plaintext=True)
        await db.invitations.insert_one(dict(inv))

        r = await client.post(ACCEPT_URL, json={"token": RAW_TOKEN, "password": PASSWORD})
        assert r.status_code == 200

        doc = await db.invitations.find_one({"id": inv["id"]})
        assert "token" not in doc
        assert doc["tokenHash"] == _sha(RAW_TOKEN)
        assert doc["status"] == "accepted"
        # Single use still holds after migration.
        again = await client.post(ACCEPT_URL, json={"token": RAW_TOKEN, "password": PASSWORD})
        assert again.status_code == 404

    async def test_legacy_lookup_migrates_even_when_not_accepted(self, db, org_id, users):
        inv = make_invitation(org_id, invited_by=users["admin"]["id"], token=RAW_TOKEN, legacy_plaintext=True)
        await db.invitations.insert_one(dict(inv))

        found = await invite_tokens.find_invitation_by_token(db.invitations, RAW_TOKEN, {"status": "pending"})
        assert found["id"] == inv["id"] and "token" not in found
        stored = await db.invitations.find_one({"id": inv["id"]})
        assert "token" not in stored and stored["tokenHash"] == _sha(RAW_TOKEN)
        # Second lookup now resolves through the hash.
        assert (await invite_tokens.find_invitation_by_token(db.invitations, RAW_TOKEN))["id"] == inv["id"]

    @pytest.mark.parametrize("token", ["wrong-token", "", RAW_TOKEN.upper()])
    async def test_invalid_tokens_rejected(self, client, db, org_id, users, token):
        await db.invitations.insert_one(make_invitation(org_id, invited_by=users["admin"]["id"], token=RAW_TOKEN))
        r = await client.post(ACCEPT_URL, json={"token": token, "password": PASSWORD})
        assert r.status_code == 404
        assert not await db.users.find_one({"email": "invitee@example.com"})

    @pytest.mark.parametrize("legacy", [False, True])
    async def test_expired_tokens_rejected(self, client, db, org_id, users, legacy):
        inv = make_invitation(
            org_id, invited_by=users["admin"]["id"], token=RAW_TOKEN,
            expires_at=iso_in(-1), legacy_plaintext=legacy,
        )
        await db.invitations.insert_one(dict(inv))
        r = await client.post(ACCEPT_URL, json={"token": RAW_TOKEN, "password": PASSWORD})
        assert r.status_code == 400
        doc = await db.invitations.find_one({"id": inv["id"]})
        assert doc["status"] == "expired" and "token" not in doc

    async def test_non_pending_tokens_rejected(self, client, db, org_id, users):
        inv = make_invitation(org_id, invited_by=users["admin"]["id"], token=RAW_TOKEN, status="accepted")
        await db.invitations.insert_one(dict(inv))
        r = await client.post(ACCEPT_URL, json={"token": RAW_TOKEN, "password": PASSWORD})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Startup migration
# ---------------------------------------------------------------------------
class TestLegacyMigration:
    async def test_migration_hashes_all_plaintext_tokens_idempotently(self, db, org_id, users):
        admin = users["admin"]["id"]
        legacy_pending = make_invitation(org_id, admin, token="legacy-a", legacy_plaintext=True)
        legacy_accepted = make_invitation(
            org_id, admin, token="legacy-b", legacy_plaintext=True, status="accepted", email="b@x.example"
        )
        modern = make_invitation(org_id, admin, token="modern-c", email="c@x.example")
        for doc in (legacy_pending, legacy_accepted, modern):
            await db.invitations.insert_one(dict(doc))
        # The pre-fix plaintext unique index, as it exists in production.
        await db.invitations.create_index([("token", 1)], unique=True, name="uq_invitations_token")

        (migration,) = [m for m in MIGRATIONS if m["name"] == "hash_invitation_tokens"]
        await migration["run"](db)
        await migration["run"](db)  # idempotent

        assert await db.invitations.count_documents({"token": {"$exists": True}}) == 0
        for doc, raw in ((legacy_pending, "legacy-a"), (legacy_accepted, "legacy-b"), (modern, "modern-c")):
            stored = await db.invitations.find_one({"id": doc["id"]})
            assert stored["tokenHash"] == _sha(raw)
            assert stored["status"] == doc["status"]
        assert "uq_invitations_token" not in await db.invitations.index_information()
