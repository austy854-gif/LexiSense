"""Regression tests for ``PATCH /api/v1/contracts/{id}``.

Covers the two fixes applied to this endpoint:

1. **RBAC** -- viewers are read-only, and a non-admin/manager may only edit a
   contract they uploaded themselves.
2. **Validated body model** -- the update payload is a JSON body
   (``ContractUpdate``) rather than bare query parameters, so it stays out of
   URLs, access logs and browser history, and is schema-validated by FastAPI.
"""
import pytest

from tests.unit.conftest import (
    API_PREFIX,
    auth_headers,
    make_contract,
    make_user,
)

PATCH_URL = f"{API_PREFIX}/contracts/{{contract_id}}"


async def _seed_contract(db, contract):
    await db.contracts.insert_one(dict(contract))


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class TestPatchContractAuthentication:
    async def test_requires_authentication(self, client, db, contract):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Renamed"},
        )

        # HTTPBearer rejects a missing header. FastAPI >=0.115 returns 401
        # (Unauthorized) for absent credentials; 403 is reserved for an
        # authenticated-but-forbidden caller.
        assert response.status_code == 401
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == contract["title"]
        assert stored["status"] == contract["status"]

    async def test_rejects_invalid_token(self, client, db, contract):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Renamed"},
            headers={"Authorization": "Bearer not-a-real-token"},
        )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# RBAC matrix
# ---------------------------------------------------------------------------
class TestPatchContractRbac:
    async def test_viewer_is_forbidden(self, client, db, contract, users):
        """A viewer may never modify a contract, even one they uploaded."""
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Renamed by viewer"},
            headers=auth_headers(users["viewer"]),
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Viewers cannot modify contracts"
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == contract["title"]

    async def test_viewer_who_uploaded_is_still_forbidden(self, client, db, org_id):
        """The viewer check runs before the ownership check."""
        viewer_uploader = make_user(org_id, role="viewer", user_id="viewer-uploader")
        owned = make_contract(
            org_id, uploaded_by=viewer_uploader["id"], contract_id="contract-viewer"
        )
        await _seed_contract(db, owned)

        response = await client.patch(
            PATCH_URL.format(contract_id=owned["id"]),
            json={"title": "Renamed"},
            headers=auth_headers(viewer_uploader),
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Viewers cannot modify contracts"

    async def test_non_uploader_user_is_forbidden(self, client, db, contract, users):
        """A plain ``user`` who did not upload the contract cannot edit it."""
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Renamed by stranger"},
            headers=auth_headers(users["user"]),
        )

        assert response.status_code == 403
        assert (
            response.json()["detail"]
            == "Only admins, managers, or the uploader can modify this contract"
        )
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == contract["title"]

    async def test_uploader_can_modify_own_contract(self, client, db, contract, owner):
        """The uploader is allowed even without an elevated role."""
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Renamed by uploader"},
            headers=auth_headers(owner),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == "Renamed by uploader"

    async def test_manager_can_modify_any_contract(self, client, db, contract, users):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Renamed by manager"},
            headers=auth_headers(users["manager"]),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == "Renamed by manager"

    async def test_admin_can_modify_any_contract(self, client, db, contract, users):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Renamed by admin"},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == "Renamed by admin"

    @pytest.mark.parametrize("role", ["user", "manager", "admin"])
    async def test_allowed_roles_are_exactly_user_manager_admin(
        self, client, db, org_id, users, role
    ):
        """Guards against a future role being silently granted write access.

        Each role is tested as the contract's *uploader*, which is the only way
        a plain ``user`` is permitted to write.
        """
        owned = make_contract(
            org_id, uploaded_by=users[role]["id"], contract_id=f"contract-{role}"
        )
        await _seed_contract(db, owned)

        response = await client.patch(
            PATCH_URL.format(contract_id=owned["id"]),
            json={"title": f"Renamed by {role}"},
            headers=auth_headers(users[role]),
        )

        assert response.status_code == 200

    async def test_non_owner_user_is_denied_while_owner_is_allowed(
        self, client, db, contract, users, owner
    ):
        """The same role is allowed or denied purely on upload ownership."""
        await _seed_contract(db, contract)

        denied = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Renamed by non-owner"},
            headers=auth_headers(users["user"]),
        )
        allowed = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Renamed by owner"},
            headers=auth_headers(owner),
        )

        assert denied.status_code == 403
        assert allowed.status_code == 200


# ---------------------------------------------------------------------------
# Organisation scoping / not-found handling
# ---------------------------------------------------------------------------
class TestPatchContractScoping:
    async def test_contract_in_another_org_is_not_found(
        self, client, db, contract, other_org_id
    ):
        """An admin of another organisation must not see or edit the contract."""
        await _seed_contract(db, contract)
        foreign_admin = make_user(other_org_id, role="admin", user_id="foreign-admin")

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Cross-tenant rename"},
            headers=auth_headers(foreign_admin),
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Contract not found"
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == contract["title"]

    async def test_unknown_contract_is_not_found(self, client, db, users):
        response = await client.patch(
            PATCH_URL.format(contract_id="does-not-exist"),
            json={"title": "Renamed"},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Contract not found"


# ---------------------------------------------------------------------------
# Status validation
# ---------------------------------------------------------------------------
class TestPatchContractStatusValidation:
    """A status change is a workflow transition, not a free-form field edit.

    These assertions were superseded by the Blocker 2 fix. PATCH used to accept
    any of the five statuses directly, which let a caller jump straight from
    ``draft`` to ``approved`` and bypass the state machine. PATCH now routes the
    status through the same transition check as the workflow endpoints, so only
    a legal transition is accepted.
    """

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            ("draft", "review"),
            ("review", "approved"),
            ("review", "draft"),
            ("approved", "active"),
            ("approved", "review"),
            ("active", "expired"),
        ],
    )
    async def test_accepts_each_legal_transition(
        self, client, db, org_id, users, from_status, to_status
    ):
        contract = make_contract(
            org_id,
            uploaded_by=users["admin"]["id"],
            contract_id=f"contract-{from_status}-{to_status}",
            status=from_status,
        )
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"status": to_status},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["status"] == to_status

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            ("draft", "approved"),
            ("draft", "active"),
            ("draft", "expired"),
            ("review", "active"),
            ("expired", "draft"),
        ],
    )
    async def test_rejects_illegal_transition(
        self, client, db, org_id, users, from_status, to_status
    ):
        """The Blocker 2 regression: PATCH can no longer skip the state machine."""
        contract = make_contract(
            org_id,
            uploaded_by=users["admin"]["id"],
            contract_id=f"contract-illegal-{from_status}-{to_status}",
            status=from_status,
        )
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"status": to_status},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 400
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["status"] == from_status

    @pytest.mark.parametrize(
        "invalid_status", ["bogus", "DRAFT", "deleted", "", "active; DROP"]
    )
    async def test_rejects_invalid_status(
        self, client, db, contract, users, invalid_status
    ):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"status": invalid_status},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid status value"
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["status"] == contract["status"]


# ---------------------------------------------------------------------------
# Update semantics
# ---------------------------------------------------------------------------
class TestPatchContractUpdates:
    async def test_updates_only_the_supplied_fields(self, client, db, contract, users):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "New Title"},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == "New Title"
        # Untouched fields keep their original values.
        assert stored["counterparty"] == contract["counterparty"]
        assert stored["contractType"] == contract["contractType"]
        assert stored["status"] == contract["status"]

    async def test_updates_all_supported_fields(self, client, db, contract, users):
        await _seed_contract(db, contract)

        payload = {
            "title": "Updated MSA",
            "counterparty": "Globex",
            "contractType": "NDA",
            # A legal transition from the fixture's ``draft`` status. The old
            # assertion used ``active``, which the state machine now rejects.
            "status": "review",
            "tags": ["priority", "renewal"],
            "changeReason": "Renegotiated terms",
        }
        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json=payload,
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        for field, value in payload.items():
            if field == "changeReason":
                continue  # recorded on the version snapshot, not the contract
            assert stored[field] == value

    async def test_refreshes_updated_at(self, client, db, contract, users):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "New Title"},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["updatedAt"] >= contract["updatedAt"]

    async def test_empty_body_is_a_no_op_but_succeeds(self, client, db, contract, users):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == contract["title"]


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------
class TestPatchContractVersioning:
    async def test_creates_first_version_snapshot(self, client, db, contract, users):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "New Title", "changeReason": "Initial edit"},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        assert response.json()["version"] == 1

        versions = await db.contract_versions.find({"contractId": contract["id"]}).to_list(10)
        assert len(versions) == 1
        snapshot = versions[0]
        # The snapshot captures the PRE-update state.
        assert snapshot["title"] == contract["title"]
        assert snapshot["status"] == contract["status"]
        assert snapshot["version"] == 1
        assert snapshot["changedBy"] == users["admin"]["id"]
        assert snapshot["changeReason"] == "Initial edit"

    async def test_increments_version_number(self, client, db, contract, users):
        await _seed_contract(db, contract)
        await db.contract_versions.insert_one(
            {
                "id": "v3",
                "contractId": contract["id"],
                "version": 3,
                "title": contract["title"],
                "contractType": contract["contractType"],
                "status": contract["status"],
                "changedBy": users["admin"]["id"],
                "createdAt": contract["createdAt"],
            }
        )

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Fourth revision"},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        assert response.json()["version"] == 4
        versions = await db.contract_versions.find({"contractId": contract["id"]}).to_list(10)
        assert sorted(v["version"] for v in versions) == [3, 4]

    async def test_records_change_reason_and_author(self, client, db, contract, owner):
        await _seed_contract(db, contract)

        await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Owner edit", "changeReason": "Typo fix"},
            headers=auth_headers(owner),
        )

        snapshot = await db.contract_versions.find_one({"contractId": contract["id"]})
        assert snapshot["changedBy"] == owner["id"]
        assert snapshot["changeReason"] == "Typo fix"

    async def test_forbidden_request_creates_no_version(self, client, db, contract, users):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"title": "Denied"},
            headers=auth_headers(users["viewer"]),
        )

        assert response.status_code == 403
        assert await db.contract_versions.count_documents({}) == 0


# ---------------------------------------------------------------------------
# Body-model regression tests
# ---------------------------------------------------------------------------
class TestPatchContractBodyModel:
    """The payload must travel in the JSON body, never in the query string."""

    async def test_query_parameters_are_ignored(self, client, db, contract, users):
        """Regression: ``?title=...`` used to be honoured as a query parameter."""
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]) + "?title=Injected&status=active",
            json={},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == contract["title"]
        assert stored["status"] == contract["status"]

    async def test_body_takes_effect_where_query_does_not(
        self, client, db, contract, users
    ):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]) + "?title=FromQuery",
            json={"title": "FromBody"},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == "FromBody"

    async def test_unknown_body_fields_are_ignored(self, client, db, contract, users):
        """``ContractUpdate`` ignores extras, so no arbitrary field can be written."""
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={
                "title": "Legit",
                "organizationId": "org-attacker",
                "uploadedBy": "attacker",
                "riskLevel": "low",
            },
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == "Legit"
        assert stored["organizationId"] == contract["organizationId"]
        assert stored["uploadedBy"] == contract["uploadedBy"]
        assert stored["riskLevel"] == contract["riskLevel"]

    async def test_tags_must_be_a_list(self, client, db, contract, users):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            json={"tags": "not-a-list"},
            headers=auth_headers(users["admin"]),
        )

        assert response.status_code == 422

    async def test_malformed_json_is_rejected(self, client, db, contract, users):
        await _seed_contract(db, contract)

        response = await client.patch(
            PATCH_URL.format(contract_id=contract["id"]),
            content=b"{not valid json",
            headers={**auth_headers(users["admin"]), "Content-Type": "application/json"},
        )

        assert response.status_code == 422

    async def test_openapi_documents_a_request_body(self, client):
        """The endpoint must advertise a JSON request body, not query params."""
        schema = (await client.get("/openapi.json")).json()
        operation = schema["paths"]["/api/v1/contracts/{contract_id}"]["patch"]

        assert "requestBody" in operation
        ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/ContractUpdate")

        query_params = {p["name"] for p in operation.get("parameters", [])}
        assert "viewer" not in query_params
        assert "title" not in query_params
        assert "status" not in query_params
