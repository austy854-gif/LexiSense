"""Tests for ``POST /api/v1/team/accept-invite/preview``.

The accept page calls this before showing the sign-up form so it can tell the
invitee which team and role they are joining, and show a clear state for
invalid, expired or already-used links. It must be read-only.
"""
from tests.unit.conftest import API_PREFIX, iso_in, make_invitation

PREVIEW_URL = f"{API_PREFIX}/team/accept-invite/preview"
ACCEPT_URL = f"{API_PREFIX}/team/accept-invite"


async def _seed(db, org_id, users, **kwargs):
    await db.organizations.insert_one({"id": org_id, "name": "Acme Studio"})
    await db.users.insert_one(dict(users["admin"]))
    invitation = make_invitation(org_id, invited_by=users["admin"]["id"], **kwargs)
    await db.invitations.insert_one(dict(invitation))
    return invitation


async def test_pending_invitation_returns_details(client, db, org_id, users):
    inv = await _seed(db, org_id, users, token="tok-p", role="manager")
    response = await client.post(PREVIEW_URL, json={"token": "tok-p"})
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "email": inv["email"],
        "accountExists": False,
        "organizationName": "Acme Studio",
        "inviterName": "Test User",
        "role": "manager",
        "expiresAt": inv["expiresAt"],
    }
    # Never echo the secret or internal ids.
    assert "token" not in body and "organizationId" not in body


async def test_preview_is_read_only(client, db, org_id, users):
    inv = await _seed(db, org_id, users, token="tok-ro")
    await client.post(PREVIEW_URL, json={"token": "tok-ro"})
    stored = await db.invitations.find_one({"id": inv["id"]})
    assert stored["status"] == "pending"
    assert await db.users.find_one({"email": inv["email"]}) is None


async def test_unknown_token_is_404(client, db):
    response = await client.post(PREVIEW_URL, json={"token": "nope"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Invalid or expired invitation"


async def test_expired_invitation_is_400(client, db, org_id, users):
    await _seed(db, org_id, users, token="tok-e", expires_at=iso_in(-1))
    response = await client.post(PREVIEW_URL, json={"token": "tok-e"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Invitation has expired"


async def test_invitation_marked_expired_is_400(client, db, org_id, users):
    await _seed(db, org_id, users, token="tok-me", status="expired")
    response = await client.post(PREVIEW_URL, json={"token": "tok-me"})
    assert response.status_code == 400


async def test_accepted_invitation_is_409(client, db, org_id, users):
    await _seed(db, org_id, users, token="tok-a", status="accepted")
    response = await client.post(PREVIEW_URL, json={"token": "tok-a"})
    assert response.status_code == 409
    assert response.json()["detail"] == "Invitation has already been accepted"


async def test_after_accepting_preview_reports_409(client, db, org_id, users):
    await _seed(db, org_id, users, token="tok-flow")
    ok = await client.post(ACCEPT_URL, json={"token": "tok-flow", "password": "Sup3rSecret!"})
    assert ok.status_code == 200
    response = await client.post(PREVIEW_URL, json={"token": "tok-flow"})
    assert response.status_code == 409


async def test_unknown_stored_role_degrades_to_default(client, db, org_id, users):
    await _seed(db, org_id, users, token="tok-r", role="superuser")
    response = await client.post(PREVIEW_URL, json={"token": "tok-r"})
    assert response.json()["role"] == "user"


async def test_token_must_be_in_body(client, db, org_id, users):
    await _seed(db, org_id, users, token="tok-q")
    response = await client.post(f"{PREVIEW_URL}?token=tok-q")
    assert response.status_code == 422


async def test_empty_token_is_rejected(client):
    response = await client.post(PREVIEW_URL, json={"token": ""})
    assert response.status_code == 422


async def test_does_not_require_authentication(client, db, org_id, users):
    await _seed(db, org_id, users, token="tok-anon")
    response = await client.post(PREVIEW_URL, json={"token": "tok-anon"})
    assert response.status_code == 200


async def test_is_rate_limited(client, db):
    codes = [
        (await client.post(PREVIEW_URL, json={"token": f"guess-{i}"})).status_code
        for i in range(12)
    ]
    assert 429 in codes


async def test_reports_when_the_invitee_already_has_an_account(client, db, org_id, users):
    from tests.unit.conftest import make_user
    inv = await _seed(db, org_id, users, token="tok-acct", email="has-account@example.com")
    await db.users.insert_one(make_user("org-other", email=inv["email"]))
    response = await client.post(PREVIEW_URL, json={"token": "tok-acct"})
    assert response.json()["accountExists"] is True
