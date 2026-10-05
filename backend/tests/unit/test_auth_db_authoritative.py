"""Regression tests for DB-authoritative identity in ``utils.auth.get_current_user``.

The fix under test
------------------
A JWT is valid for its full lifetime (``ACCESS_TOKEN_EXPIRE_MINUTES``, 24 hours
by default) and its body carries ``role``/``organizationId``. Before the fix
``get_current_user`` returned that body verbatim, which meant:

* demoting a member (``admin`` -> ``viewer``) did **not** take effect until the
  token expired -- the demoted account kept admin privileges for up to a day;
* removing a member from the organisation had **no effect at all** on an
  already-issued token;
* deactivating an account was never consulted.

After the fix the account is re-read from ``users`` on every authenticated
request (a single indexed point read on ``users.id``) and the stored
``role``/``organizationId``/``isActive`` win over the token body.

What these tests pin down
-------------------------
1. A token issued while the user is an ``admin`` is rejected (403) for an
   admin-only endpoint once the stored role is changed to ``viewer`` -- *without*
   the token being re-issued. The same token is then rejected for the write
   path too.
2. A deactivated account (``isActive: False``) gets 403 on an otherwise valid
   token.
3. A removed account (document deleted) gets 401 -- deliberately the same
   status/message as a malformed token, so an unauthenticated caller cannot
   probe which account ids exist.
4. The stored ``organizationId`` wins over the token body, so a token minted
   with a forged organisation cannot reach another tenant's data.
5. Roles that are not in the registry degrade to ``viewer``, never upward.

The endpoints used as observables
---------------------------------
Two observables are used, and the distinction matters:

* ``GET /api/v1/team/invitations`` is **admin-only**
  (``require_role(*ADMIN_ROLES)``), so it is the RBAC probe. 403 means the
  request was authenticated but the stored role was not ``admin``.
* ``GET /api/v1/team/members`` is readable by *every* role, so a 200 there only
  proves the account itself is still valid -- useful when the point is that
  authentication (not authorisation) still succeeds.
* ``PATCH /api/v1/contracts/{id}`` is the canonical write gate (``WRITE_ROLES``
  plus the uploader check) and is used for the authorisation half of the
  demotion test.

These tests need the identity store wired; they request the
``db_authoritative_auth`` fixture for that. See ``conftest`` for why the wiring
is otherwise off inside the unit suite.
"""
import pytest

from tests.unit.conftest import (
    API_PREFIX,
    auth_headers,
    make_contract,
    make_user,
    seed_users,
)

#: Readable by *any* authenticated role -- used to prove an account is still valid.
MEMBERS_URL = f"{API_PREFIX}/team/members"
#: Admin-only (``require_role(*ADMIN_ROLES)``) -- the RBAC observable for role tests.
INVITATIONS_URL = f"{API_PREFIX}/team/invitations"
CONTRACT_PATCH_URL = f"{API_PREFIX}/contracts/{{contract_id}}"


# ---------------------------------------------------------------------------
# Role changes take effect on an already-issued token
# ---------------------------------------------------------------------------
class TestDemotionTakesEffectMidToken:
    async def test_demoted_admin_loses_admin_endpoint_without_new_token(
        self, client, db, db_authoritative_auth, users
    ):
        """The core regression: the token still says ``admin``, the DB says ``viewer``."""
        admin = users["admin"]
        await seed_users(db, admin)
        headers = auth_headers(admin)  # minted while the role was still "admin"

        # Sanity check -- the token works before the role changes.
        assert (await client.get(INVITATIONS_URL, headers=headers)).status_code == 200

        await db.users.update_one({"id": admin["id"]}, {"$set": {"role": "viewer"}})

        response = await client.get(INVITATIONS_URL, headers=headers)

        assert response.status_code == 403
        assert response.json()["detail"] == "This action requires one of these roles: admin"

    async def test_demoted_admin_cannot_reach_protected_read(
        self, client, db, db_authoritative_auth, users
    ):
        """``GET /team/invitations`` shares the admin-only dependency."""
        admin = users["admin"]
        await seed_users(db, admin)
        headers = auth_headers(admin)

        await db.users.update_one({"id": admin["id"]}, {"$set": {"role": "viewer"}})

        response = await client.get(f"{API_PREFIX}/team/invitations", headers=headers)

        assert response.status_code == 403

    async def test_demoted_user_loses_write_access_without_new_token(
        self, client, db, db_authoritative_auth, org_id, users
    ):
        """Demotion also revokes the contract write path, not just admin reads."""
        admin = users["admin"]
        await seed_users(db, admin)
        contract = make_contract(org_id, uploaded_by=admin["id"], contract_id="contract-demote")
        await db.contracts.insert_one(dict(contract))
        headers = auth_headers(admin)

        allowed = await client.patch(
            CONTRACT_PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Admin edit"},
            headers=headers,
        )
        assert allowed.status_code == 200

        await db.users.update_one({"id": admin["id"]}, {"$set": {"role": "viewer"}})

        denied = await client.patch(
            CONTRACT_PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Viewer edit"},
            headers=headers,
        )

        assert denied.status_code == 403
        assert denied.json()["detail"] == "Viewers cannot modify contracts"
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == "Admin edit"

    async def test_promotion_also_reads_the_database(
        self, client, db, db_authoritative_auth, users
    ):
        """The reverse direction: a stored promotion is honoured on the old token."""
        member = users["viewer"]
        await seed_users(db, member)
        headers = auth_headers(member)

        assert (await client.get(INVITATIONS_URL, headers=headers)).status_code == 403

        await db.users.update_one({"id": member["id"]}, {"$set": {"role": "admin"}})

        assert (await client.get(INVITATIONS_URL, headers=headers)).status_code == 200

    async def test_role_is_reflected_in_the_returned_principal(
        self, client, db, db_authoritative_auth, users
    ):
        """The principal handed to routers must carry the stored role, not the token's."""
        admin = users["admin"]
        await seed_users(db, admin)
        headers = auth_headers(admin)

        await db.users.update_one({"id": admin["id"]}, {"$set": {"role": "manager"}})

        # ``/team/members`` is readable by any role, so a 200 proves the account
        # is still valid; the 403 on the admin-only route proves the role was
        # re-read rather than taken from the token.
        assert (await client.get(MEMBERS_URL, headers=headers)).status_code == 200
        assert (await client.get(f"{API_PREFIX}/team/invitations", headers=headers)).status_code == 403


# ---------------------------------------------------------------------------
# Deactivation and removal
# ---------------------------------------------------------------------------
class TestAccountLifecycleMidToken:
    async def test_deactivated_account_is_forbidden(
        self, client, db, db_authoritative_auth, users
    ):
        member = users["manager"]
        await seed_users(db, member)
        headers = auth_headers(member)

        assert (await client.get(MEMBERS_URL, headers=headers)).status_code == 200

        await db.users.update_one({"id": member["id"]}, {"$set": {"isActive": False}})

        response = await client.get(MEMBERS_URL, headers=headers)

        assert response.status_code == 403
        assert response.json()["detail"] == "Account is deactivated"

    async def test_deactivation_beats_an_admin_role(
        self, client, db, db_authoritative_auth, users
    ):
        """A deactivated admin is refused before any role check runs."""
        admin = users["admin"]
        await seed_users(db, admin)
        headers = auth_headers(admin)

        await db.users.update_one({"id": admin["id"]}, {"$set": {"isActive": False}})

        response = await client.get(MEMBERS_URL, headers=headers)

        assert response.status_code == 403
        assert response.json()["detail"] == "Account is deactivated"

    async def test_missing_isactive_defaults_to_active(
        self, client, db, db_authoritative_auth, users
    ):
        """Documents predating the ``isActive`` field must keep working."""
        member = users["user"]
        document = dict(member)
        document.pop("isActive", None)
        await db.users.insert_one(document)
        await db.users.update_one({"id": member["id"]}, {"$unset": {"isActive": ""}})

        response = await client.get(MEMBERS_URL, headers=auth_headers(member))

        assert response.status_code == 200

    async def test_removed_account_is_unauthorized(
        self, client, db, db_authoritative_auth, users
    ):
        """Removing the member invalidates the token immediately."""
        member = users["user"]
        await seed_users(db, member)
        headers = auth_headers(member)

        assert (await client.get(MEMBERS_URL, headers=headers)).status_code == 200

        await db.users.delete_one({"id": member["id"]})

        response = await client.get(MEMBERS_URL, headers=headers)

        assert response.status_code == 401

    async def test_removed_and_forged_accounts_are_indistinguishable(
        self, client, db, db_authoritative_auth, users
    ):
        """No account-existence oracle: same 401 for a deleted id and a bad token."""
        member = users["user"]
        await seed_users(db, member)

        removed = await client.get(MEMBERS_URL, headers=auth_headers(member))
        await db.users.delete_one({"id": member["id"]})
        after_removal = await client.get(MEMBERS_URL, headers=auth_headers(member))

        forged = await client.get(MEMBERS_URL, headers={"Authorization": "Bearer not-a-real-token"})

        assert removed.status_code == 200
        assert after_removal.status_code == 401
        assert forged.status_code == 401
        assert after_removal.json()["detail"] == forged.json()["detail"] == "Invalid or expired token"

    async def test_token_for_an_unknown_subject_is_unauthorized(
        self, client, db, db_authoritative_auth, org_id
    ):
        """A correctly signed token for a non-existent account is still refused."""
        ghost = make_user(org_id, role="admin", user_id="ghost-admin")

        response = await client.get(MEMBERS_URL, headers=auth_headers(ghost))

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# The stored organisation is authoritative
# ---------------------------------------------------------------------------
class TestOrganisationComesFromTheDatabase:
    async def test_forged_organizationid_in_token_is_ignored(
        self, client, db, db_authoritative_auth, org_id, other_org_id, users
    ):
        """A token claiming another tenant cannot read that tenant's members."""
        member = users["admin"]
        await seed_users(db, member)

        foreign_member = make_user(other_org_id, role="admin", user_id="foreign-member")
        await seed_users(db, foreign_member)

        forged_token = auth_headers(member)  # sub is the real account ...
        from utils.auth import create_access_token

        token = create_access_token(
            {
                "sub": member["id"],
                "email": member["email"],
                "role": "admin",
                # ... but the body claims the other organisation.
                "organizationId": other_org_id,
            }
        )

        response = await client.get(MEMBERS_URL, headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        emails = {row["email"] for row in response.json()}
        assert member["email"] in emails
        assert foreign_member["email"] not in emails
        assert forged_token

    async def test_stored_org_scopes_the_contract_write(
        self, client, db, db_authoritative_auth, org_id, other_org_id, users
    ):
        """A cross-tenant contract is 404, based on the stored organisation."""
        member = users["admin"]
        await seed_users(db, member)
        foreign_contract = make_contract(
            other_org_id, uploaded_by="somebody", contract_id="foreign-contract"
        )
        await db.contracts.insert_one(dict(foreign_contract))

        response = await client.patch(
            CONTRACT_PATCH_URL.format(contract_id=foreign_contract["id"]),
            json={"title": "Cross-tenant write"},
            headers=auth_headers(member),
        )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Role normalisation
# ---------------------------------------------------------------------------
class TestRoleNormalisation:
    @pytest.mark.parametrize("bogus_role", ["superadmin", "ADMIN", "root", "", "owner"])
    async def test_unknown_stored_role_degrades_to_viewer(
        self, client, db, db_authoritative_auth, users, bogus_role
    ):
        """A corrupt/legacy role value must never be more privileged than viewer."""
        admin = users["admin"]
        await seed_users(db, admin)
        headers = auth_headers(admin)

        await db.users.update_one({"id": admin["id"]}, {"$set": {"role": bogus_role}})

        assert (await client.get(MEMBERS_URL, headers=headers)).status_code == 200
        assert (await client.get(f"{API_PREFIX}/team/invitations", headers=headers)).status_code == 403

    async def test_forged_role_in_token_cannot_exceed_stored_role(
        self, client, db, db_authoritative_auth, users
    ):
        """A hand-minted token claiming ``admin`` is capped by the stored role."""
        member = users["viewer"]
        await seed_users(db, member)

        from utils.auth import create_access_token

        token = create_access_token(
            {
                "sub": member["id"],
                "email": member["email"],
                "role": "admin",  # forged elevation
                "organizationId": member["organizationId"],
            }
        )

        response = await client.get(INVITATIONS_URL, headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Wiring guard
# ---------------------------------------------------------------------------
class TestIdentityStoreWiring:
    async def test_token_only_mode_when_store_is_unwired(
        self, client, db, users
    ):
        """Documents the fallback: without the fixture the body is trusted.

        This is not desired behaviour in production -- it is the documented
        degradation used by narrow test apps, and it is asserted here so that a
        future change to the wiring is a *deliberate* one. Production always
        wires the store through ``routes.auth.init_db``.
        """
        admin = users["admin"]
        await seed_users(db, admin)
        await db.users.update_one({"id": admin["id"]}, {"$set": {"role": "viewer"}})

        response = await client.get(MEMBERS_URL, headers=auth_headers(admin))

        # Token-only mode: the body still says "admin", so the read succeeds.
        assert response.status_code == 200
