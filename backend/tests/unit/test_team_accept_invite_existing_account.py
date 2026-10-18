"""``POST /team/accept-invite`` when the invitee already has an account.

Previously the invite was marked accepted and a generic success returned, but
the person was never added to the team. Now:

* already in the inviting team -> idempotent no-op (invite consumed, account
  unchanged, repeatable without error);
* in another team -> proves ownership with the account's current password,
  then moves to the inviting team with the invite's role.
"""
from tests.unit.conftest import API_PREFIX, make_invitation, make_user
from utils.auth import verify_password

ACCEPT_URL = f"{API_PREFIX}/team/accept-invite"
PASSWORD = "Test1234!"  # make_user's default password


async def _seed(db, org_id, users, user, **kwargs):
    await db.users.insert_one(dict(user))
    invitation = make_invitation(
        org_id, invited_by=users["admin"]["id"], email=user["email"], **kwargs
    )
    await db.invitations.insert_one(dict(invitation))
    return invitation


async def test_account_in_another_team_joins_with_invite_role(client, db, org_id, users):
    user = make_user("org-other", role="admin", user_id="u-ext", email="ext@example.com")
    inv = await _seed(db, org_id, users, user, token="tok-move", role="manager")

    response = await client.post(ACCEPT_URL, json={"token": "tok-move", "password": PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "ext@example.com"
    assert body["existingAccount"] is True and body["alreadyMember"] is False
    stored = await db.users.find_one({"id": "u-ext"})
    assert stored["organizationId"] == org_id
    assert stored["role"] == "manager"
    assert verify_password(PASSWORD, stored["passwordHash"])  # password untouched
    assert await db.users.count_documents({"email": "ext@example.com"}) == 1
    assert (await db.invitations.find_one({"id": inv["id"]}))["status"] == "accepted"


async def test_wrong_password_does_not_move_the_account(client, db, org_id, users):
    user = make_user("org-other", role="user", user_id="u-ext2", email="ext2@example.com")
    inv = await _seed(db, org_id, users, user, token="tok-wrong")

    response = await client.post(ACCEPT_URL, json={"token": "tok-wrong", "password": "not-the-password"})

    # 403, never 401 (the SPA treats 401 as an expired session).
    assert response.status_code == 403
    stored = await db.users.find_one({"id": "u-ext2"})
    assert stored["organizationId"] == "org-other"
    assert (await db.invitations.find_one({"id": inv["id"]}))["status"] == "pending"


async def test_already_a_member_is_idempotent(client, db, org_id, users):
    member = make_user(org_id, role="admin", user_id="u-mem", email="member@example.com")
    inv = await _seed(db, org_id, users, member, token="tok-member", role="viewer")

    # Password is irrelevant for a no-op; nothing about the account changes.
    response = await client.post(ACCEPT_URL, json={"token": "tok-member", "password": "whatever123"})

    assert response.status_code == 200
    assert response.json()["alreadyMember"] is True
    stored = await db.users.find_one({"id": "u-mem"})
    assert stored["role"] == "admin"  # not downgraded to the invite role
    assert stored["organizationId"] == org_id
    assert stored["passwordHash"] == member["passwordHash"]
    assert (await db.invitations.find_one({"id": inv["id"]}))["status"] == "accepted"


async def test_second_invite_for_an_existing_member_is_also_a_no_op(client, db, org_id, users):
    member = make_user(org_id, role="user", user_id="u-mem2", email="member2@example.com")
    await _seed(db, org_id, users, member, token="tok-a")
    second = make_invitation(org_id, invited_by=users["admin"]["id"], email=member["email"], token="tok-b")
    await db.invitations.insert_one(dict(second))

    first = await client.post(ACCEPT_URL, json={"token": "tok-a", "password": PASSWORD})
    again = await client.post(ACCEPT_URL, json={"token": "tok-b", "password": PASSWORD})

    assert first.status_code == again.status_code == 200
    assert again.json()["alreadyMember"] is True
    assert (await db.users.find_one({"id": "u-mem2"}))["role"] == "user"


async def test_reusing_the_same_token_after_joining_is_rejected(client, db, org_id, users):
    user = make_user("org-other", user_id="u-ext3", email="ext3@example.com")
    await _seed(db, org_id, users, user, token="tok-once")

    first = await client.post(ACCEPT_URL, json={"token": "tok-once", "password": PASSWORD})
    second = await client.post(ACCEPT_URL, json={"token": "tok-once", "password": PASSWORD})

    assert first.status_code == 200
    assert second.status_code == 404


async def test_sole_admin_of_a_team_with_members_is_refused(client, db, org_id, users):
    admin = make_user("org-other", role="admin", user_id="u-boss", email="boss@example.com")
    await db.users.insert_one(make_user("org-other", role="user", user_id="u-staff"))
    inv = await _seed(db, org_id, users, admin, token="tok-boss")

    response = await client.post(ACCEPT_URL, json={"token": "tok-boss", "password": PASSWORD})

    assert response.status_code == 409
    assert (await db.users.find_one({"id": "u-boss"}))["organizationId"] == "org-other"
    assert (await db.invitations.find_one({"id": inv["id"]}))["status"] == "pending"


async def test_admin_with_a_co_admin_can_move(client, db, org_id, users):
    admin = make_user("org-other", role="admin", user_id="u-boss2", email="boss2@example.com")
    await db.users.insert_one(make_user("org-other", role="admin", user_id="u-coadmin"))
    await _seed(db, org_id, users, admin, token="tok-boss2", role="user")

    response = await client.post(ACCEPT_URL, json={"token": "tok-boss2", "password": PASSWORD})

    assert response.status_code == 200
    assert (await db.users.find_one({"id": "u-boss2"}))["role"] == "user"


async def test_deactivated_account_is_refused(client, db, org_id, users):
    user = make_user("org-other", user_id="u-off", email="off@example.com")
    user["isActive"] = False
    await _seed(db, org_id, users, user, token="tok-off")

    response = await client.post(ACCEPT_URL, json={"token": "tok-off", "password": PASSWORD})

    assert response.status_code == 403
    assert (await db.users.find_one({"id": "u-off"}))["organizationId"] == "org-other"


async def test_existing_account_join_works_with_a_legacy_plaintext_invite(client, db, org_id, users):
    user = make_user("org-other", user_id="u-leg", email="leg@example.com")
    await _seed(db, org_id, users, user, token="tok-leg", legacy_plaintext=True, role="manager")

    response = await client.post(ACCEPT_URL, json={"token": "tok-leg", "password": PASSWORD})

    assert response.status_code == 200
    stored = await db.users.find_one({"id": "u-leg"})
    assert stored["organizationId"] == org_id and stored["role"] == "manager"
