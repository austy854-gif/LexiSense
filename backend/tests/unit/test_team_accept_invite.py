"""Regression tests for ``POST /api/v1/team/accept-invite``.

Covers the fix that moved the new user's password **out of the query string
and into a validated JSON body** (``AcceptInvitationRequest``).

Passing a password as a query parameter leaks it into access logs, proxy logs
and browser history, so these tests assert both that the body is honoured and
that the query string is ignored.
"""
import pytest

from tests.unit.conftest import (
    API_PREFIX,
    iso_in,
    make_invitation,
    make_user,
)
from utils.auth import verify_password

ACCEPT_URL = f"{API_PREFIX}/team/accept-invite"

VALID_PASSWORD = "Sup3rSecret!"


async def _seed_invitation(db, invitation):
    await db.invitations.insert_one(dict(invitation))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
class TestAcceptInviteSuccess:
    async def test_accepts_invitation_with_json_body(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-valid"
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            ACCEPT_URL,
            json={
                "token": "tok-valid",
                "password": VALID_PASSWORD,
                "firstName": "Ada",
                "lastName": "Lovelace",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "message": "Account created successfully",
            "email": invitation["email"],
        }

    async def test_creates_the_user_with_hashed_password(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-hash"
        )
        await _seed_invitation(db, invitation)

        await client.post(
            ACCEPT_URL,
            json={"token": "tok-hash", "password": VALID_PASSWORD},
        )

        created = await db.users.find_one({"email": invitation["email"]})
        assert created is not None
        # The plaintext password must never be persisted.
        assert created["passwordHash"] != VALID_PASSWORD
        assert verify_password(VALID_PASSWORD, created["passwordHash"])

    async def test_inherits_role_and_organization_from_invitation(
        self, client, db, org_id, users
    ):
        invitation = make_invitation(
            org_id,
            invited_by=users["admin"]["id"],
            token="tok-role",
            role="manager",
        )
        await _seed_invitation(db, invitation)

        await client.post(
            ACCEPT_URL,
            json={"token": "tok-role", "password": VALID_PASSWORD},
        )

        created = await db.users.find_one({"email": invitation["email"]})
        assert created["role"] == "manager"
        assert created["organizationId"] == org_id

    async def test_stores_optional_names(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-names"
        )
        await _seed_invitation(db, invitation)

        await client.post(
            ACCEPT_URL,
            json={
                "token": "tok-names",
                "password": VALID_PASSWORD,
                "firstName": "Grace",
                "lastName": "Hopper",
            },
        )

        created = await db.users.find_one({"email": invitation["email"]})
        assert created["firstName"] == "Grace"
        assert created["lastName"] == "Hopper"

    async def test_names_are_optional(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-noname"
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            ACCEPT_URL,
            json={"token": "tok-noname", "password": VALID_PASSWORD},
        )

        assert response.status_code == 200
        created = await db.users.find_one({"email": invitation["email"]})
        assert created["firstName"] is None
        assert created["lastName"] is None

    async def test_marks_invitation_as_accepted(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-accept"
        )
        await _seed_invitation(db, invitation)

        await client.post(
            ACCEPT_URL,
            json={"token": "tok-accept", "password": VALID_PASSWORD},
        )

        stored = await db.invitations.find_one({"id": invitation["id"]})
        assert stored["status"] == "accepted"

    async def test_does_not_require_authentication(self, client, db, org_id, users):
        """The invitee has no account yet, so the endpoint must stay public."""
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-public"
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            ACCEPT_URL,
            json={"token": "tok-public", "password": VALID_PASSWORD},
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Body-model regression tests
# ---------------------------------------------------------------------------
class TestAcceptInviteBodyModel:
    """The password must travel in the JSON body, never in the query string."""

    async def test_password_in_query_string_is_not_accepted(
        self, client, db, org_id, users
    ):
        """Regression: ``?password=...`` used to create the account."""
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-query"
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            f"{ACCEPT_URL}?token=tok-query&password={VALID_PASSWORD}",
            json={},
        )

        # ``password`` is a required body field, so the request is rejected.
        assert response.status_code == 422
        assert await db.users.find_one({"email": invitation["email"]}) is None
        stored = await db.invitations.find_one({"id": invitation["id"]})
        assert stored["status"] == "pending"

    async def test_query_string_password_is_ignored_when_body_is_present(
        self, client, db, org_id, users
    ):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-both"
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            f"{ACCEPT_URL}?password=QueryPassword1!",
            json={"token": "tok-both", "password": VALID_PASSWORD},
        )

        assert response.status_code == 200
        created = await db.users.find_one({"email": invitation["email"]})
        # The body wins; the query-string value is never used.
        assert verify_password(VALID_PASSWORD, created["passwordHash"])
        assert not verify_password("QueryPassword1!", created["passwordHash"])

    async def test_token_in_query_string_is_not_accepted(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-qtoken"
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            f"{ACCEPT_URL}?token=tok-qtoken",
            json={"password": VALID_PASSWORD},
        )

        assert response.status_code == 422
        assert await db.users.find_one({"email": invitation["email"]}) is None

    async def test_missing_password_is_rejected(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-nopw"
        )
        await _seed_invitation(db, invitation)

        response = await client.post(ACCEPT_URL, json={"token": "tok-nopw"})

        assert response.status_code == 422
        assert await db.users.find_one({"email": invitation["email"]}) is None

    async def test_missing_token_is_rejected(self, client, db):
        response = await client.post(ACCEPT_URL, json={"password": VALID_PASSWORD})

        assert response.status_code == 422

    @pytest.mark.parametrize("short_password", ["", "short", "1234567"])
    async def test_password_shorter_than_eight_chars_is_rejected(
        self, client, db, org_id, users, short_password
    ):
        """``AcceptInvitationRequest.password`` enforces ``min_length=8``."""
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-short"
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            ACCEPT_URL,
            json={"token": "tok-short", "password": short_password},
        )

        assert response.status_code == 422
        assert await db.users.find_one({"email": invitation["email"]}) is None

    async def test_eight_char_password_is_accepted(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-eight"
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            ACCEPT_URL,
            json={"token": "tok-eight", "password": "12345678"},
        )

        assert response.status_code == 200

    async def test_malformed_json_is_rejected(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-badjson"
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            ACCEPT_URL,
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422

    async def test_openapi_documents_a_request_body(self, client):
        """The endpoint must advertise a JSON request body, not query params."""
        schema = (await client.get("/openapi.json")).json()
        operation = schema["paths"]["/api/v1/team/accept-invite"]["post"]

        assert "requestBody" in operation
        ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/AcceptInvitationRequest")

        query_params = {p["name"] for p in operation.get("parameters", [])}
        assert "password" not in query_params
        assert "token" not in query_params


# ---------------------------------------------------------------------------
# Invalid / expired / reused invitations
# ---------------------------------------------------------------------------
class TestAcceptInviteInvalidInvitations:
    async def test_unknown_token_is_not_found(self, client, db):
        response = await client.post(
            ACCEPT_URL,
            json={"token": "never-issued", "password": VALID_PASSWORD},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Invalid or expired invitation"

    async def test_expired_invitation_is_rejected(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id,
            invited_by=users["admin"]["id"],
            token="tok-expired",
            expires_at=iso_in(-1),
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            ACCEPT_URL,
            json={"token": "tok-expired", "password": VALID_PASSWORD},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invitation has expired"
        assert await db.users.find_one({"email": invitation["email"]}) is None

    async def test_expired_invitation_is_marked_expired(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id,
            invited_by=users["admin"]["id"],
            token="tok-expired-mark",
            expires_at=iso_in(-1),
        )
        await _seed_invitation(db, invitation)

        await client.post(
            ACCEPT_URL,
            json={"token": "tok-expired-mark", "password": VALID_PASSWORD},
        )

        stored = await db.invitations.find_one({"id": invitation["id"]})
        assert stored["status"] == "expired"

    async def test_invitation_expiring_in_the_future_is_accepted(
        self, client, db, org_id, users
    ):
        invitation = make_invitation(
            org_id,
            invited_by=users["admin"]["id"],
            token="tok-future",
            expires_at=iso_in(1),
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            ACCEPT_URL,
            json={"token": "tok-future", "password": VALID_PASSWORD},
        )

        assert response.status_code == 200

    async def test_token_cannot_be_reused(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id, invited_by=users["admin"]["id"], token="tok-reuse"
        )
        await _seed_invitation(db, invitation)

        first = await client.post(
            ACCEPT_URL,
            json={"token": "tok-reuse", "password": VALID_PASSWORD},
        )
        second = await client.post(
            ACCEPT_URL,
            json={"token": "tok-reuse", "password": "AnotherPass1!"},
        )

        assert first.status_code == 200
        assert second.status_code == 404
        # Only one account was created.
        assert await db.users.count_documents({"email": invitation["email"]}) == 1

    async def test_already_accepted_invitation_is_not_found(
        self, client, db, org_id, users
    ):
        invitation = make_invitation(
            org_id,
            invited_by=users["admin"]["id"],
            token="tok-accepted",
            status="accepted",
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            ACCEPT_URL,
            json={"token": "tok-accepted", "password": VALID_PASSWORD},
        )

        assert response.status_code == 404

    async def test_cancelled_invitation_is_not_found(self, client, db, org_id, users):
        invitation = make_invitation(
            org_id,
            invited_by=users["admin"]["id"],
            token="tok-cancelled",
            status="cancelled",
        )
        await _seed_invitation(db, invitation)

        response = await client.post(
            ACCEPT_URL,
            json={"token": "tok-cancelled", "password": VALID_PASSWORD},
        )

        assert response.status_code == 404

    async def test_does_not_touch_an_existing_account(self, client, db, org_id, users):
        """An invitation for an email that already has an account is still
        processed by this endpoint, but must not overwrite the existing user."""
        existing = make_user(org_id, role="admin", user_id="existing-admin")
        await db.users.insert_one(dict(existing))

        invitation = make_invitation(
            org_id,
            invited_by=users["admin"]["id"],
            token="tok-dupe",
            email=existing["email"],
        )
        await _seed_invitation(db, invitation)

        await client.post(
            ACCEPT_URL,
            json={"token": "tok-dupe", "password": VALID_PASSWORD},
        )

        stored = await db.users.find_one({"id": existing["id"]})
        assert stored["role"] == "admin"
        assert stored["passwordHash"] == existing["passwordHash"]
