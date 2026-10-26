"""Coverage for the contract-intelligence and billing fixes.

These stay free of the LLM and of S3: they lock the pure rules the upload
path and the auth dependency now depend on.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from routes import billing as billing_route
from services.ai_service import chunk_plan, merge_analyses, select_chat_context
from utils.auth import create_access_token, get_current_user
from utils.billing_access import grace_ends_at, upload_gate
from utils.contract_fields import coerce_iso_date, fields_from_analysis
from utils.uploads import UploadRejected, attachment_filename, validate_upload
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract, seed_users


def test_iso_dates_reject_prose_and_impossible_days():
    assert coerce_iso_date("2026-10-26") == "2026-10-26"
    assert coerce_iso_date("October 26, 2026") is None
    assert coerce_iso_date("2026-02-31") is None
    assert coerce_iso_date(20261026) is None


def test_fields_from_analysis_drops_garbage_and_keeps_real_dates():
    fields = fields_from_analysis({
        "riskLevel": "High",
        "value": {"amount": 10},
        "dates": {"effectiveDate": "2026-01-01", "expiryDate": "next year"},
    })
    assert fields == {
        "riskLevel": "high",
        "effectiveDate": "2026-01-01",
        "expiryDate": None,
        "value": None,
    }
    numeric = fields_from_analysis({"value": 1200, "riskLevel": "nope", "dates": {}})
    assert numeric["value"] == "1200"
    assert numeric["riskLevel"] is None


def test_long_contract_keeps_the_ending_and_is_marked_partial():
    text = ("A" * 8000) + ("B" * 8000) + ("C" * 8000) + "TAIL-CLAUSE"
    chunks, coverage = chunk_plan(text, chunk_size=8000, overlap=0, max_chunks=2)
    assert coverage["partial"] is True
    assert len(chunks) == 2
    assert chunks[0].startswith("A")
    assert chunks[-1].endswith("TAIL-CLAUSE")
    assert coverage["analyzedCharacters"] < coverage["totalCharacters"]


def test_short_contract_is_not_partial():
    chunks, coverage = chunk_plan("short agreement", max_chunks=8)
    assert chunks == ["short agreement"]
    assert coverage["partial"] is False
    assert coverage["analyzedCharacters"] == len("short agreement")


def test_merge_lets_the_later_chunk_win_dates_and_keeps_the_worst_risk():
    merged = merge_analyses([
        {"summary": "Opening.", "riskLevel": "low", "dates": {"expiryDate": "2026-01-01"}, "value": "100"},
        {
            "summary": "Term.",
            "riskLevel": "high",
            "dates": {"expiryDate": "2027-06-01", "effectiveDate": "not a date"},
            "value": "$10,000",
            "risks": [{"risk": "Auto renew", "severity": "high", "recommendation": "Diary it"}],
        },
    ])
    assert merged["dates"]["expiryDate"] == "2027-06-01"
    assert merged["dates"]["effectiveDate"] is None
    assert merged["riskLevel"] == "high"
    assert merged["value"] == "$10,000"
    assert merged["risks"][0]["risk"] == "Auto renew"


def test_chat_context_includes_both_ends_when_the_document_is_long():
    text = "START " + ("middle " * 4000) + " indemnity cap END"
    context, partial = select_chat_context(text, "what is the indemnity cap?", budget=4000)
    assert partial is True
    assert "START" in context
    assert "END" in context
    assert "indemnity" in context.lower()


def test_upload_sniff_rejects_a_mislabeled_pdf_and_html_text():
    with pytest.raises(UploadRejected):
        validate_upload("contract.pdf", b"<html>not a pdf</html>")
    with pytest.raises(UploadRejected):
        validate_upload("notes.html", b"hello")
    content_type, ext = validate_upload("contract.pdf", b"%PDF-1.4 rest")
    assert (content_type, ext) == ("application/pdf", "pdf")
    content_type, ext = validate_upload("notes.txt", "plain contract".encode())
    assert (content_type, ext) == ("text/plain", "txt")
    assert attachment_filename('evil"/../../name.txt', "txt") == "evil_name.txt"


def test_past_due_is_allowed_during_grace_and_read_only_after():
    now = datetime(2026, 10, 26, tzinfo=timezone.utc)
    recent = {
        "subscriptionTier": "team",
        "billingStatus": "past_due",
        "stripeCustomerId": "cus_123",
        "pastDueSince": (now - timedelta(days=2)).isoformat(),
    }
    stale = dict(recent, pastDueSince=(now - timedelta(days=30)).isoformat())
    assert upload_gate(recent, now) == "allow"
    assert upload_gate(stale, now) == "readonly"
    assert upload_gate({"subscriptionTier": "team", "billingStatus": "active"}, now) == "allow"
    assert upload_gate({"subscriptionTier": "cancelled", "billingStatus": "cancelled"}, now) == "readonly"
    assert upload_gate({"subscriptionTier": "trial", "billingStatus": "trialing"}, now) == "trial"
    assert grace_ends_at(recent) is not None
    assert grace_ends_at({"billingStatus": "active"}) is None


async def test_revoked_token_and_rotated_session_are_rejected(db, db_authoritative_auth):
    now = datetime.now(timezone.utc).isoformat()
    user = {
        "id": "user-1",
        "email": "owner@example.com",
        "passwordHash": "not-checked",
        "role": "admin",
        "organizationId": "org-1",
        "isActive": True,
        "createdAt": now,
    }
    await seed_users(db, user)
    token = create_access_token({
        "sub": user["id"],
        "email": user["email"],
        "role": user["role"],
        "organizationId": user["organizationId"],
        "sv": 0,
    })
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert (await get_current_user(creds))["sub"] == user["id"]

    from utils.auth import decode_token
    jti = decode_token(token)["jti"]
    await db.revoked_tokens.insert_one({"jti": jti, "exp": datetime.now(timezone.utc)})
    with pytest.raises(HTTPException) as revoked:
        await get_current_user(creds)
    assert revoked.value.status_code == 401

    await db.revoked_tokens.delete_many({})
    await db.users.update_one({"id": user["id"]}, {"$set": {"sessionVersion": 1}})
    with pytest.raises(HTTPException) as rotated:
        await get_current_user(creds)
    assert rotated.value.status_code == 401

    fresh = create_access_token({
        "sub": user["id"],
        "email": user["email"],
        "role": user["role"],
        "organizationId": user["organizationId"],
        "sv": 1,
    })
    again = await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=fresh))
    assert again["role"] == "admin"


async def test_paid_upload_failure_does_not_drain_the_trial_counter(db):
    billing_route.init_db(db)
    await db.organization_billing.insert_one({
        "organizationId": "org-paid",
        "subscriptionTier": "team",
        "billingStatus": "active",
        "trialContractsUsed": 3,
        "trialContractsLimit": 25,
    })
    ok, message = await billing_route.reserve_trial_upload_atomic("org-paid")
    assert (ok, message) == (True, "")
    await billing_route.release_trial_upload("org-paid")
    stored = await db.organization_billing.find_one({"organizationId": "org-paid"})
    assert stored["trialContractsUsed"] == 3


async def test_cancelled_org_cannot_edit_a_contract(client, db, db_authoritative_auth):
    now = datetime.now(timezone.utc).isoformat()
    user = {
        "id": "user-1",
        "email": "owner@example.com",
        "passwordHash": "not-checked",
        "role": "admin",
        "organizationId": "org-1",
        "isActive": True,
        "createdAt": now,
    }
    await seed_users(db, user)
    contract = make_contract("org-1", user["id"], contract_id="c-1")
    await db.contracts.insert_one(contract)
    await db.organization_billing.insert_one({
        "organizationId": "org-1",
        "subscriptionTier": "cancelled",
        "billingStatus": "cancelled",
        "trialContractsUsed": 0,
        "trialContractsLimit": 25,
    })
    response = await client.patch(
        f"{API_PREFIX}/contracts/c-1",
        headers=auth_headers(user),
        json={"title": "Renamed"},
    )
    assert response.status_code == 403
    assert "read-only" in response.text.lower()
