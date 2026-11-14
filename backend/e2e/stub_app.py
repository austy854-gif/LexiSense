"""LexiSense API with every third party stubbed, for the Playwright suite.

Run (from ``backend/``)::

    E2E_STUB_SERVICES=1 uvicorn e2e.stub_app:app --port 8000

It is the real ``server.app`` (real routes, auth, policy, billing rules and
Mongo) with these swapped for in-process fakes, so the suite needs no secrets
and never reaches the network:

* AI analysis and chat return a fixed, realistic analysis;
* S3 storage keeps objects in memory;
* email is captured in an outbox instead of being sent through Resend;
* Stripe is never configured (``STRIPE_SECRET_KEY`` unset), so checkout and
  portal stay off; the browser tests stub those redirects at the network layer.

It also adds ``/__e2e/*`` helpers (reset + seed fixture data, mint a session
for a fixture user, read the outbox). Importing this module refuses to run
unless ``E2E_STUB_SERVICES=1`` and ``ENVIRONMENT`` is not production, so it can
never be served by accident.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List

if os.environ.get("E2E_STUB_SERVICES") != "1" or os.environ.get(
    "ENVIRONMENT", ""
).lower() in ("production", "prod"):
    raise RuntimeError(
        "e2e.stub_app is for end-to-end tests only: set E2E_STUB_SERVICES=1 "
        "and a non-production ENVIRONMENT"
    )

from fastapi import APIRouter, HTTPException, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import server  # noqa: E402

# /__e2e/reset empties every collection and has no auth. Refuse to start
# unless the resolved database is clearly a throwaway e2e one, so a direct
# run with DB_NAME unset can never wipe `lexisense` or a URL's default DB.
if "e2e" not in server.DB_NAME.lower():
    raise RuntimeError(
        f"e2e.stub_app refuses database {server.DB_NAME!r}: set DB_NAME to a "
        "name containing 'e2e' (the launcher default is lexisense_e2e)."
    )
from routes import contracts as contracts_route  # noqa: E402
from routes import team as team_route  # noqa: E402
from services import email_service  # noqa: E402
from utils import rbac  # noqa: E402
from utils.auth import create_access_token, hash_password  # noqa: E402

app = server.app
db = server.db

PASSWORD = "E2e-Passw0rd!"
ACME = "org-e2e-acme"
OVERDUE = "org-e2e-overdue"

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
ANALYSIS = {
    "summary": (
        "Master services agreement between Acme Legal and Northwind Traders "
        "for 12 months of consulting at a fixed fee."
    ),
    "parties": ["Acme Legal Inc.", "Northwind Traders Ltd."],
    "riskLevel": "medium",
    "value": "$120,000",
    "dates": {"effectiveDate": "2027-01-01", "expiryDate": "2027-12-31"},
    "keyTerms": [
        {"term": "Payment terms", "description": "Net 30 from invoice date"},
        {"term": "Termination", "description": "60 days written notice"},
    ],
    "risks": [
        {
            "risk": "Uncapped liability for data breaches",
            "severity": "high",
            "recommendation": "Cap liability at 12 months of fees",
        }
    ],
    "recommendations": ["Add a mutual limitation of liability clause"],
}

STORAGE: Dict[str, bytes] = {}
OUTBOX: List[dict] = []


async def _analyze(text):
    return dict(ANALYSIS)


async def _chat(*args, **kwargs):
    return "The agreement runs for 12 months from 1 January 2027."


async def _upload(content, filename, org_id, content_type="", extension=None):
    # A UUID, not len(STORAGE): deleting an object must not let the next
    # upload reuse (and overwrite) another object's key.
    key = f"{org_id}/e2e-{uuid.uuid4().hex}.{extension or 'bin'}"
    STORAGE[key] = content
    return key


async def _delete(key, *args, **kwargs):
    STORAGE.pop(key, None)
    return True


async def _presign(key, *args, **kwargs):
    return f"/__e2e/files/{key}"


async def _send_invitation_email(to_email, inviter_name, organization_name, role, token):
    OUTBOX.append(
        {
            "to": to_email,
            "template": "invitation",
            "organization": organization_name,
            "role": role,
            "acceptUrl": f"/accept-invite?token={token}",
        }
    )
    return {"status": "success", "email_id": f"e2e-{len(OUTBOX)}"}


contracts_route.analyze_contract = _analyze
contracts_route.get_chat_response = _chat
contracts_route.upload_file_to_s3 = _upload
contracts_route.delete_file_from_s3 = _delete
contracts_route.purge_uploaded_object = _delete
contracts_route.generate_presigned_url = _presign
team_route.send_invitation_email = _send_invitation_email
email_service.send_invitation_email = _send_invitation_email


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------
def _iso(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


USERS = [
    # Acme Legal: a trial organization with one account per role.
    ("admin@acme.example.com", "admin", ACME, "Ada", "Admin"),
    ("manager@acme.example.com", "manager", ACME, "Max", "Manager"),
    ("user@acme.example.com", "user", ACME, "Uma", "User"),
    ("viewer@acme.example.com", "viewer", ACME, "Vic", "Viewer"),
    # Overdue LLC: a paid organization 30 days past due (read-only).
    ("admin@overdue.example.com", "admin", OVERDUE, "Olga", "Owner"),
]

async def _seed() -> None:
    # Empty every collection but keep the indexes bootstrap built.
    for name in await db.list_collection_names():
        if not name.startswith("system."):
            await db[name].delete_many({})
    STORAGE.clear()
    OUTBOX.clear()
    for limiter in (rbac.AUTH_RATE_LIMITER, rbac.INVITE_RATE_LIMITER):
        limiter.reset()

    now = _iso()
    await db.organizations.insert_many(
        [
            {"id": ACME, "name": "Acme Legal", "createdAt": now, "updatedAt": now},
            {"id": OVERDUE, "name": "Overdue LLC", "createdAt": now, "updatedAt": now},
        ]
    )
    await db.organization_billing.insert_many(
        [
            {
                # A paying Team org: the trial's 3-seat cap would already be
                # full with the four fixture users, and invites are under test.
                "organizationId": ACME,
                "subscriptionTier": "team",
                "billingStatus": "active",
                "stripePriceId": os.environ.get(
                    "STRIPE_PRICE_TEAM_MONTHLY", "price_e2e_team_monthly"
                ),
                "stripeCustomerId": "cus_e2e_acme",
                "stripeSubscriptionId": "sub_e2e_acme",
                "trialContractsUsed": 0,
                "trialContractsLimit": 25,
                "createdAt": now,
                "updatedAt": now,
            },
            {
                "organizationId": OVERDUE,
                "subscriptionTier": "team",
                "billingStatus": "past_due",
                "stripePriceId": os.environ.get(
                    "STRIPE_PRICE_TEAM_MONTHLY", "price_e2e_team_monthly"
                ),
                "stripeCustomerId": "cus_e2e_overdue",
                "pastDueSince": _iso(days=-30),
                "trialContractsUsed": 0,
                "trialContractsLimit": 25,
                "createdAt": now,
                "updatedAt": now,
            },
        ]
    )
    password_hash = hash_password(PASSWORD)
    await db.users.insert_many(
        [
            {
                "id": f"u-e2e-{email.split('@')[0]}-{org[-4:]}",
                "email": email,
                "passwordHash": password_hash,
                "firstName": first,
                "lastName": last,
                "role": role,
                "organizationId": org,
                "isActive": True,
                "sessionVersion": 0,
                "createdAt": now,
                "updatedAt": now,
            }
            for email, role, org, first, last in USERS
        ]
    )
    # One contract in the overdue org so its read-only views have content.
    await db.contracts.insert_one(
        {
            "id": "c-e2e-overdue",
            "organizationId": OVERDUE,
            "uploadedBy": "u-e2e-admin-rdue",
            "title": "Overdue LLC Lease",
            "counterparty": "Landlord Co",
            "contractType": "Lease",
            "status": "draft",
            "riskLevel": "low",
            "originalText": "Lease between Overdue LLC and Landlord Co.",
            "aiAnalysis": {"summary": "A 3-year office lease."},
            "aiAnalysisStatus": "success",
            "storageKey": None,
            "fileName": "lease.txt",
            "fileSize": 42,
            "mimeType": "text/plain",
            "tags": [],
            "version": 1,
            "createdAt": now,
            "updatedAt": now,
        }
    )


# ---------------------------------------------------------------------------
# /__e2e helpers
# ---------------------------------------------------------------------------
e2e_router = APIRouter(prefix="/__e2e", include_in_schema=False)


class SessionRequest(BaseModel):
    email: str


@e2e_router.post("/reset")
async def reset():
    await _seed()
    return {"ok": True, "password": PASSWORD}


@e2e_router.post("/session")
async def session(body: SessionRequest):
    """A token + user for a fixture account (skips the login rate limit)."""
    user = await db.users.find_one({"email": body.email}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="No such fixture user")
    token = create_access_token(
        data={
            "sub": user["id"],
            "email": user["email"],
            "role": user["role"],
            "organizationId": user["organizationId"],
            "sv": user.get("sessionVersion", 0),
        }
    )
    user.pop("passwordHash", None)
    return {"access_token": token, "user": user}


@e2e_router.get("/outbox")
async def outbox():
    return OUTBOX


@e2e_router.get("/files/{key:path}")
async def stored_file(key: str):
    """Serve an object from the in-memory store, standing in for a presigned
    S3 URL. Inline (like S3's default), so the browser can show it."""
    content = STORAGE.get(key)
    if content is None:
        raise HTTPException(status_code=404, detail="Not found")
    media_type = {"pdf": "application/pdf", "txt": "text/plain; charset=utf-8"}.get(
        key.rsplit(".", 1)[-1], "application/octet-stream"
    )
    filename = key.rsplit("/", 1)[-1]
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


app.include_router(e2e_router)

# The SPA catch-all route in server.py was registered first and would swallow
# /__e2e/*; move it to the end so the helpers above win.
_spa = [r for r in app.router.routes if getattr(r, "name", "") == "serve_spa"]
for route in _spa:
    app.router.routes.remove(route)
    app.router.routes.append(route)
