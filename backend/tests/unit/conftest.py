"""Shared pytest fixtures for the LexiSense backend unit test-suite.

These tests exercise the FastAPI routers **in-process** (via
``httpx.ASGITransport``) against an in-memory MongoDB provided by
``mongomock-motor``.  No live server, no real database and no network access
are required, so the suite runs in CI out of the box.

The fixtures here back the regression tests for three security fixes:

* ``PATCH /api/v1/contracts/{id}`` -- role-based access control plus a
  validated JSON body model (``ContractUpdate``).
* ``POST /api/v1/team/accept-invite`` -- the password moved from a query
  parameter to a validated JSON body (``AcceptInvitationRequest``).
* ``utils.auth.get_current_user`` -- identity/role are re-read from the
  database on every request instead of being trusted from the token body.

The third item needs an extra switch. ``utils.auth`` keeps the identity store
in a module global that is only wired by ``routes.auth.init_db`` -- which a
narrow test app mounting a single router never calls. As long as the store is
unwired, ``get_current_user`` degrades to token-only verification, so
DB-authoritative behaviour is invisible to the suite. Tests that exercise it
request the ``db_authoritative_auth`` fixture, which wires the store through
``monkeypatch`` and therefore cannot leak into the other test modules.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# The environment must be configured BEFORE any application module is
# imported: ``utils.auth`` raises at import time when JWT_SECRET is missing.
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "lexisense_test")

from fastapi import APIRouter, FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

from models.contract import Contract  # noqa: E402
from models.invitation import Invitation  # noqa: E402
from routes import contracts as contracts_route  # noqa: E402
from routes import team as team_route  # noqa: E402
from services import audit_service, email_service  # noqa: E402
from utils import rbac as rbac_module  # noqa: E402
from utils.auth import create_access_token, hash_password  # noqa: E402

API_PREFIX = "/api/v1"

#: Roles the application understands, ordered from least to most privileged.
ROLES = ("viewer", "user", "manager", "admin")


# ---------------------------------------------------------------------------
# Application / database fixtures
# ---------------------------------------------------------------------------
def _build_app() -> FastAPI:
    """Mount only the routers under test, behind the real ``/api/v1`` prefix."""
    app = FastAPI(title="LexiSense Test App")
    api_router = APIRouter(prefix=API_PREFIX)
    api_router.include_router(contracts_route.router)
    api_router.include_router(team_route.router)
    app.include_router(api_router)
    return app


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Reset the process-global rate limiters around every test.

    The limiters are module-level singletons keyed by client identity, and the
    in-process test client always presents the same client host. Without a
    reset, earlier tests in a module would consume the budget and later ones
    would (correctly) receive 429 -- a real cross-test coupling that has nothing
    to do with the behaviour under test. Dedicated rate-limit tests drive the
    limiter directly instead.
    """
    rbac_module.AUTH_RATE_LIMITER.reset()
    rbac_module.INVITE_RATE_LIMITER.reset()
    yield
    rbac_module.AUTH_RATE_LIMITER.reset()
    rbac_module.INVITE_RATE_LIMITER.reset()


@pytest.fixture
def db():
    """A fresh in-memory MongoDB database for every test."""
    client = AsyncMongoMockClient()
    return client["lexisense_test"]


@pytest.fixture
def app(db, monkeypatch):
    """The FastAPI app wired to the in-memory database.

    Every module that keeps a module-level ``db`` handle is re-pointed at the
    mock, and outbound email is stubbed so no network call is attempted.
    """
    contracts_route.init_db(db)
    team_route.init_db(db)
    audit_service.init_db(db)

    async def _no_send_email(*args, **kwargs):
        return {"id": "test-email", "status": "sent"}

    # ``team.py`` imports the symbol directly, so patch it on both modules.
    monkeypatch.setattr(email_service, "send_invitation_email", _no_send_email)
    monkeypatch.setattr(team_route, "send_invitation_email", _no_send_email)

    return _build_app()


@pytest.fixture
async def client(app):
    """An async HTTP client bound to the in-process ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
def db_authoritative_auth(db, monkeypatch):
    """Make ``utils.auth`` resolve identity/role from the in-memory database.

    This mirrors exactly what ``routes.auth.init_db`` does in production
    (``init_identity_store(database)`` binds ``database.users``). It is applied
    via ``monkeypatch`` so the module global is restored after the test -- a
    plain call would leak the wiring into later modules and break the existing
    token-only tests, which never seed a ``users`` collection.
    """
    from utils import auth as auth_module

    monkeypatch.setattr(auth_module, "_users_collection", db.users)
    return db.users


# ---------------------------------------------------------------------------
# Data factories
# ---------------------------------------------------------------------------
def make_user(org_id, role="user", user_id=None, email=None, password="Test1234!"):
    """Build a user document shaped exactly like ``models.user.User``."""
    uid = user_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": uid,
        "email": email or f"{uid[:8]}@example.com",
        "passwordHash": hash_password(password),
        "firstName": "Test",
        "lastName": "User",
        "role": role,
        "organizationId": org_id,
        "isActive": True,
        "lastLogin": None,
        "createdAt": now,
        "updatedAt": now,
    }


def make_contract(org_id, uploaded_by, contract_id=None, **overrides):
    """Build a contract document shaped exactly like ``models.contract.Contract``."""
    contract = Contract(
        organizationId=org_id,
        uploadedBy=uploaded_by,
        title="Master Services Agreement",
        counterparty="Acme Corp",
        contractType="MSA",
        status="draft",
    ).model_dump()
    if contract_id:
        contract["id"] = contract_id
    contract.update(overrides)
    return contract


def make_invitation(
    org_id,
    invited_by,
    token=None,
    expires_at=None,
    status="pending",
    role="user",
    email=None,
):
    """Build an invitation document shaped like ``models.invitation.Invitation``."""
    invitation = Invitation(
        organizationId=org_id,
        email=email or "invitee@example.com",
        role=role,
        invitedBy=invited_by,
    ).model_dump()
    if token:
        invitation["token"] = token
    if expires_at:
        invitation["expiresAt"] = expires_at
    invitation["status"] = status
    return invitation


def auth_headers(user):
    """Mint a real JWT for ``user`` and wrap it in an Authorization header."""
    token = create_access_token(
        {
            "sub": user["id"],
            "email": user["email"],
            "role": user["role"],
            "organizationId": user["organizationId"],
        }
    )
    return {"Authorization": f"Bearer {token}"}


def iso_in(days):
    """An ISO-8601 UTC timestamp ``days`` from now (negative = in the past)."""
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


async def seed_users(db, *users):
    """Insert user documents into the in-memory database.

    The role fixtures only build detached dictionaries. The DB-authoritative
    auth tests need the documents to exist, because ``get_current_user``
    re-reads the account on every request.
    """
    for user in users:
        await db.users.insert_one(dict(user))


# ---------------------------------------------------------------------------
# Convenience fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def org_id():
    return "org-alpha"


@pytest.fixture
def other_org_id():
    return "org-beta"


@pytest.fixture
def users(org_id):
    """One user per role, all inside ``org_id``."""
    return {role: make_user(org_id, role=role, user_id=f"user-{role}") for role in ROLES}


@pytest.fixture
def owner(org_id):
    """A plain ``user``-role member who uploaded the fixture contract.

    Kept separate from the ``users`` role matrix so the RBAC tests can tell
    "the uploader" apart from "a non-uploader holding the same role".
    """
    return make_user(org_id, role="user", user_id="user-owner")


@pytest.fixture
def contract(org_id, owner):
    """A contract in ``org_id`` uploaded by ``owner``."""
    return make_contract(org_id, uploaded_by=owner["id"], contract_id="contract-1")
