"""Regression tests for team membership management.

Covers the two approved backend fixes:

1. ``PATCH /api/v1/team/members/{id}/role`` took the new role as a **query
   parameter**. A query parameter is the wrong transport for a state change: it
   is validated only inside the handler (after the request has been routed and
   logged), it lands in access / proxy logs and browser history, and it
   advertises no request body in OpenAPI. The fix validates a JSON body model
   (``MemberRoleUpdate.role``) **before** the handler runs.

2. ``DELETE /api/v1/team/members/{id}`` **hard-deleted** the user row, which
   orphaned every ``contract_versions`` row that member had authored: the
   version listing resolves ``changedBy`` against ``users`` and would render a
   missing author (``changedByEmail`` = ``None``) forever. The fix reassigns
   those rows to the acting admin as part of the same operation.
"""
from tests.unit.conftest import (
    API_PREFIX,
    auth_headers,
    make_contract,
    make_user,
)

ROLE_URL = f"{API_PREFIX}/team/members/{{member_id}}/role"
MEMBER_URL = f"{API_PREFIX}/team/members/{{member_id}}"


async def _seed(db, *users):
    for user in users:
        await db.users.insert_one(dict(user))


async def _seed_version(db, contract, changed_by, version=1):
    """Insert one historical contract version authored by ``changed_by``."""
    from models.contract_version import ContractVersion

    doc = ContractVersion(
        contractId=contract["id"],
        version=version,
        title=contract["title"],
        contractType=contract["contractType"],
        status=contract["status"],
        changedBy=changed_by,
    ).model_dump()
    await db.contract_versions.insert_one(doc)
    return doc


# ---------------------------------------------------------------------------
# PATCH /team/members/{id}/role -- validated JSON body
# ---------------------------------------------------------------------------
class TestUpdateMemberRoleBodyModel:
    async def test_role_in_body_is_applied(self, client, db, org_id):
        admin = make_user(org_id, role="admin", user_id="user-admin")
        target = make_user(org_id, role="user", user_id="user-target")
        await _seed(db, admin, target)

        response = await client.patch(
            ROLE_URL.format(member_id=target["id"]),
            json={"role": "manager"},
            headers=auth_headers(admin),
        )

        assert response.status_code == 200
        stored = await db.users.find_one({"id": target["id"]})
        assert stored["role"] == "manager"

    async def test_query_parameter_role_is_ignored(self, client, db, org_id):
        """A stray ``?role=`` query parameter must not decide the outcome."""
        admin = make_user(org_id, role="admin", user_id="user-admin")
        target = make_user(org_id, role="user", user_id="user-target")
        await _seed(db, admin, target)

        response = await client.patch(
            ROLE_URL.format(member_id=target["id"]) + "?role=manager",
            json={"role": "viewer"},
            headers=auth_headers(admin),
        )

        assert response.status_code == 200
        stored = await db.users.find_one({"id": target["id"]})
        assert stored["role"] == "viewer"

    async def test_query_only_role_does_not_change_role(self, client, db, org_id):
        admin = make_user(org_id, role="admin", user_id="user-admin")
        target = make_user(org_id, role="user", user_id="user-target")
        await _seed(db, admin, target)

        response = await client.patch(
            ROLE_URL.format(member_id=target["id"]) + "?role=manager",
            headers=auth_headers(admin),
        )

        assert response.status_code == 422
        stored = await db.users.find_one({"id": target["id"]})
        assert stored["role"] == "user"

    async def test_invalid_role_is_rejected(self, client, db, org_id):
        admin = make_user(org_id, role="admin", user_id="user-admin")
        target = make_user(org_id, role="user", user_id="user-target")
        await _seed(db, admin, target)

        response = await client.patch(
            ROLE_URL.format(member_id=target["id"]),
            json={"role": "superuser"},
            headers=auth_headers(admin),
        )

        assert response.status_code == 400
        stored = await db.users.find_one({"id": target["id"]})
        assert stored["role"] == "user"

    async def test_unknown_body_field_cannot_redirect_org(self, client, db, org_id):
        admin = make_user(org_id, role="admin", user_id="user-admin")
        target = make_user(org_id, role="user", user_id="user-target")
        await _seed(db, admin, target)

        response = await client.patch(
            ROLE_URL.format(member_id=target["id"]),
            json={"role": "viewer", "organizationId": "org-evil"},
            headers=auth_headers(admin),
        )

        assert response.status_code == 200
        stored = await db.users.find_one({"id": target["id"]})
        assert stored["role"] == "viewer"
        assert stored["organizationId"] == org_id

    async def test_self_role_change_is_rejected(self, client, db, org_id):
        admin = make_user(org_id, role="admin", user_id="user-admin")
        await _seed(db, admin)

        response = await client.patch(
            ROLE_URL.format(member_id=admin["id"]),
            json={"role": "viewer"},
            headers=auth_headers(admin),
        )

        assert response.status_code == 400

    async def test_non_admin_is_forbidden(self, client, db, org_id):
        manager = make_user(org_id, role="manager", user_id="user-manager")
        target = make_user(org_id, role="user", user_id="user-target")
        await _seed(db, manager, target)

        response = await client.patch(
            ROLE_URL.format(member_id=target["id"]),
            json={"role": "admin"},
            headers=auth_headers(manager),
        )

        assert response.status_code == 403
        stored = await db.users.find_one({"id": target["id"]})
        assert stored["role"] == "user"

    async def test_cross_tenant_member_is_not_found(self, client, db, org_id, other_org_id):
        admin = make_user(org_id, role="admin", user_id="user-admin")
        foreign = make_user(other_org_id, role="user", user_id="user-foreign")
        await _seed(db, admin, foreign)

        response = await client.patch(
            ROLE_URL.format(member_id=foreign["id"]),
            json={"role": "admin"},
            headers=auth_headers(admin),
        )

        assert response.status_code == 404
        stored = await db.users.find_one({"id": foreign["id"]})
        assert stored["role"] == "user"

    async def test_openapi_advertises_a_body_not_a_role_query_param(self, client):
        schema = (await client.get("/openapi.json")).json()
        operation = schema["paths"]["/api/v1/team/members/{member_id}/role"]["patch"]

        query_names = {
            p.get("name")
            for p in operation.get("parameters", [])
            if p.get("in") == "query"
        }
        assert "role" not in query_names

        ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("MemberRoleUpdate")
        model = schema["components"]["schemas"]["MemberRoleUpdate"]
        assert "role" in model["properties"]
        assert "role" in model.get("required", [])


# ---------------------------------------------------------------------------
# DELETE /team/members/{id} -- orphaned version history is repaired
# ---------------------------------------------------------------------------
class TestRemoveMemberReassignsVersionHistory:
    async def test_versions_are_reassigned_to_acting_admin(self, client, db, org_id):
        admin = make_user(
            org_id, role="admin", user_id="user-admin", email="admin@example.com"
        )
        victim = make_user(org_id, role="user", user_id="user-victim")
        owner = make_user(org_id, role="user", user_id="user-owner")
        contract = make_contract(
            org_id, uploaded_by=owner["id"], contract_id="contract-1"
        )
        await _seed(db, admin, victim, owner)
        await db.contracts.insert_one(dict(contract))
        version = await _seed_version(db, contract, changed_by=victim["id"])

        response = await client.delete(
            MEMBER_URL.format(member_id=victim["id"]),
            headers=auth_headers(admin),
        )

        assert response.status_code == 200
        assert await db.users.count_documents({"id": victim["id"]}) == 0

        repaired = await db.contract_versions.find_one({"id": version["id"]})
        assert repaired["changedBy"] == admin["id"]

        # The listing must now resolve a readable author for that version.
        listing = await client.get(
            f"{API_PREFIX}/contracts/{contract['id']}/versions",
            headers=auth_headers(admin),
        )
        assert listing.status_code == 200
        rows = listing.json()
        assert len(rows) == 1
        assert rows[0]["changedByEmail"] == "admin@example.com"

    async def test_other_members_versions_are_untouched(self, client, db, org_id):
        admin = make_user(org_id, role="admin", user_id="user-admin")
        victim = make_user(org_id, role="user", user_id="user-victim")
        keeper = make_user(org_id, role="user", user_id="user-keeper")
        contract = make_contract(org_id, uploaded_by=keeper["id"], contract_id="contract-1")
        await _seed(db, admin, victim, keeper)
        await db.contracts.insert_one(dict(contract))
        theirs = await _seed_version(db, contract, changed_by=keeper["id"], version=1)
        mine = await _seed_version(db, contract, changed_by=victim["id"], version=2)

        response = await client.delete(
            MEMBER_URL.format(member_id=victim["id"]),
            headers=auth_headers(admin),
        )

        assert response.status_code == 200
        assert (await db.contract_versions.find_one({"id": theirs["id"]}))[
            "changedBy"
        ] == keeper["id"]
        assert (await db.contract_versions.find_one({"id": mine["id"]}))[
            "changedBy"
        ] == admin["id"]

    async def test_other_tenants_versions_are_not_touched(
        self, client, db, org_id, other_org_id
    ):
        admin = make_user(org_id, role="admin", user_id="user-admin")
        victim = make_user(org_id, role="user", user_id="user-victim")
        foreign_owner = make_user(other_org_id, role="user", user_id="user-foreign")
        foreign_contract = make_contract(
            other_org_id, uploaded_by=foreign_owner["id"], contract_id="contract-x"
        )
        await _seed(db, admin, victim, foreign_owner)
        await db.contracts.insert_one(dict(foreign_contract))
        foreign_version = await _seed_version(
            db, foreign_contract, changed_by=victim["id"]
        )

        response = await client.delete(
            MEMBER_URL.format(member_id=victim["id"]),
            headers=auth_headers(admin),
        )

        assert response.status_code == 200
        untouched = await db.contract_versions.find_one({"id": foreign_version["id"]})
        assert untouched["changedBy"] == victim["id"]

    async def test_non_admin_cannot_remove_member(self, client, db, org_id):
        manager = make_user(org_id, role="manager", user_id="user-manager")
        target = make_user(org_id, role="user", user_id="user-target")
        await _seed(db, manager, target)

        response = await client.delete(
            MEMBER_URL.format(member_id=target["id"]),
            headers=auth_headers(manager),
        )

        assert response.status_code == 403
        assert await db.users.count_documents({"id": target["id"]}) == 1

    async def test_cannot_remove_self(self, client, db, org_id):
        admin = make_user(org_id, role="admin", user_id="user-admin")
        await _seed(db, admin)

        response = await client.delete(
            MEMBER_URL.format(member_id=admin["id"]),
            headers=auth_headers(admin),
        )

        assert response.status_code == 400
        assert await db.users.count_documents({"id": admin["id"]}) == 1

    async def test_unknown_member_is_not_found(self, client, db, org_id):
        admin = make_user(org_id, role="admin", user_id="user-admin")
        await _seed(db, admin)

        response = await client.delete(
            MEMBER_URL.format(member_id="user-does-not-exist"),
            headers=auth_headers(admin),
        )

        assert response.status_code == 404
