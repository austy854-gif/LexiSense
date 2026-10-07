"""Regression tests for ``POST /api/v1/contracts/{id}/restore/{version}`` RBAC.

The defect
----------
Restoring a version *mutates the contract* (it overwrites title, status,
counterparty, dates, risk level, text and analysis), but the endpoint originally
had **no authorisation check at all**: any authenticated member of the
organisation -- including a read-only ``viewer`` -- could roll a contract back to
an arbitrary earlier version. That is a silent data-loss primitive: a viewer
could reinstate superseded terms, or revert an ``approved`` contract to ``draft``.

The fix
-------
The restore path now applies the same two-stage gate as ``PATCH /contracts/{id}``:

1. ``current_user["role"] in WRITE_ROLES`` -- ``viewer`` is rejected with 403
   ``"Viewers cannot modify contracts"``;
2. the actor must be ``admin``/``manager`` **or** the contract's ``uploadedBy``,
   otherwise 403 ``"Only admins, managers, or the uploader can restore this
   contract"``.

Ordering matters and is asserted: the contract/version lookups (404) run
*before* the role checks, so existence is never leaked to an unauthorised
caller, and the viewer gate runs *before* the ownership gate, so a viewer who
happens to be the uploader is still refused.

The matrix
----------
============================  ======================  =======
Actor                         Relation to contract    Result
============================  ======================  =======
viewer                        non-owner               403 (viewer gate)
viewer                        uploader                403 (viewer gate)
user                          non-owner               403 (ownership gate)
user                          uploader                200
manager                       non-owner               200
admin                         non-owner               200
admin (other organisation)    n/a                     404
============================  ======================  =======
"""
import pytest

from models.contract_version import ContractVersion
from tests.unit.conftest import (
    API_PREFIX,
    auth_headers,
    make_contract,
    make_user,
)

RESTORE_URL = f"{API_PREFIX}/contracts/{{contract_id}}/restore/{{version_num}}"


def make_version(contract_id, version, changed_by, **overrides):
    """Build a version document shaped like ``models.contract_version``."""
    document = ContractVersion(
        contractId=contract_id,
        version=version,
        title=f"Title at v{version}",
        counterparty="Acme Corp",
        contractType="MSA",
        status="draft",
        changedBy=changed_by,
        changeReason=f"Snapshot {version}",
    ).model_dump()
    document.update(overrides)
    return document


async def _seed(db, contract, *versions):
    """Insert a contract and its historical versions."""
    await db.contracts.insert_one(dict(contract))
    for version in versions:
        await db.contract_versions.insert_one(dict(version))


def _restore(client, contract_id, version_num, user):
    return client.post(
        RESTORE_URL.format(contract_id=contract_id, version_num=version_num),
        headers=auth_headers(user),
    )


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class TestRestoreAuthentication:
    async def test_requires_authentication(self, client, db, contract, owner):
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        response = await client.post(
            RESTORE_URL.format(contract_id=contract["id"], version_num=1)
        )

        # HTTPBearer rejects a missing header. FastAPI >=0.115 returns 401
        # (Unauthorized) for absent credentials; 403 is reserved for an
        # authenticated-but-forbidden caller.
        assert response.status_code == 401

    async def test_rejects_invalid_token(self, client, db, contract, owner):
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        response = await client.post(
            RESTORE_URL.format(contract_id=contract["id"], version_num=1),
            headers={"Authorization": "Bearer not-a-real-token"},
        )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# The RBAC matrix
# ---------------------------------------------------------------------------
class TestRestoreRbacMatrix:
    async def test_viewer_is_forbidden(self, client, db, contract, owner, users):
        """The original defect: a viewer could roll a contract back."""
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        response = await _restore(client, contract["id"], 1, users["viewer"])

        assert response.status_code == 403
        assert response.json()["detail"] == "Viewers cannot modify contracts"

    async def test_viewer_who_uploaded_is_still_forbidden(
        self, client, db, org_id, owner
    ):
        """The viewer gate runs before the ownership gate."""
        viewer_uploader = make_user(org_id, role="viewer", user_id="viewer-uploader")
        owned = make_contract(
            org_id, uploaded_by=viewer_uploader["id"], contract_id="contract-viewer-restore"
        )
        await _seed(db, owned, make_version(owned["id"], 1, viewer_uploader["id"]))

        response = await _restore(client, owned["id"], 1, viewer_uploader)

        assert response.status_code == 403
        assert response.json()["detail"] == "Viewers cannot modify contracts"

    async def test_non_uploader_user_is_forbidden(self, client, db, contract, owner, users):
        """A plain ``user`` who did not upload the contract cannot restore it."""
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        response = await _restore(client, contract["id"], 1, users["user"])

        assert response.status_code == 403
        assert (
            response.json()["detail"]
            == "Only admins, managers, or the uploader can restore this contract"
        )

    async def test_uploader_user_is_allowed(self, client, db, contract, owner):
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        response = await _restore(client, contract["id"], 1, owner)

        assert response.status_code == 200

    async def test_manager_is_allowed_for_any_contract(self, client, db, contract, owner, users):
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        response = await _restore(client, contract["id"], 1, users["manager"])

        assert response.status_code == 200

    async def test_admin_is_allowed_for_any_contract(self, client, db, contract, owner, users):
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        response = await _restore(client, contract["id"], 1, users["admin"])

        assert response.status_code == 200

    @pytest.mark.parametrize("role", ["user", "manager", "admin"])
    async def test_non_owner_matrix_is_exactly_user_denied_manager_admin_allowed(
        self, client, db, org_id, owner, role
    ):
        """A non-uploader is allowed for manager/admin and denied for plain user."""
        contract = make_contract(
            org_id, uploaded_by=owner["id"], contract_id=f"contract-nonowner-{role}"
        )
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))
        actor = make_user(org_id, role=role, user_id=f"actor-{role}")

        response = await _restore(client, contract["id"], 1, actor)

        assert response.status_code == (403 if role == "user" else 200)


# ---------------------------------------------------------------------------
# Denied requests must have no side effects
# ---------------------------------------------------------------------------
class TestRestoreDeniedHasNoSideEffects:
    @pytest.mark.parametrize("actor_fixture", ["viewer", "non_owner_user"])
    async def test_forbidden_restore_does_not_touch_the_contract(
        self, client, db, contract, owner, users, actor_fixture
    ):
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))
        actor = users["viewer"] if actor_fixture == "viewer" else users["user"]

        response = await _restore(client, contract["id"], 1, actor)

        assert response.status_code == 403
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == contract["title"]
        assert stored["status"] == contract["status"]
        assert stored["counterparty"] == contract["counterparty"]

    async def test_forbidden_restore_creates_no_snapshot(self, client, db, contract, owner, users):
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        response = await _restore(client, contract["id"], 1, users["viewer"])

        assert response.status_code == 403
        # Only the seeded v1 exists; the pre-restore snapshot must not be written.
        assert await db.contract_versions.count_documents({"contractId": contract["id"]}) == 1


# ---------------------------------------------------------------------------
# Existence handling (runs before RBAC, so existence is not leaked)
# ---------------------------------------------------------------------------
class TestRestoreExistenceHandling:
    async def test_unknown_contract_is_not_found(self, client, db, users):
        response = await _restore(client, "does-not-exist", 1, users["admin"])

        assert response.status_code == 404
        assert response.json()["detail"] == "Contract not found"

    async def test_unknown_version_is_not_found(self, client, db, contract, owner, users):
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        response = await _restore(client, contract["id"], 99, users["admin"])

        assert response.status_code == 404
        assert response.json()["detail"] == "Version not found"

    async def test_contract_in_another_org_is_not_found_for_a_viewer_too(
        self, client, db, other_org_id, users
    ):
        """Existence is checked before the role gate, so 404 (not 403) is returned."""
        foreign = make_contract(
            other_org_id, uploaded_by="someone", contract_id="foreign-restore"
        )
        await _seed(db, foreign, make_version(foreign["id"], 1, "someone"))

        response = await _restore(client, foreign["id"], 1, users["viewer"])

        assert response.status_code == 404

    async def test_admin_of_another_org_is_not_found(self, client, db, contract, owner, other_org_id):
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))
        foreign_admin = make_user(other_org_id, role="admin", user_id="foreign-admin")

        response = await _restore(client, contract["id"], 1, foreign_admin)

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Successful restore semantics
# ---------------------------------------------------------------------------
class TestRestoreSuccessSemantics:
    async def test_restores_the_target_version_fields(self, client, db, contract, owner, users):
        target = make_version(
            contract["id"],
            1,
            owner["id"],
            title="Original Title",
            status="review",
            counterparty="Globex",
            riskLevel="high",
        )
        await _seed(db, contract, target)

        response = await _restore(client, contract["id"], 1, users["admin"])

        assert response.status_code == 200
        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["title"] == "Original Title"
        assert stored["status"] == "review"
        assert stored["counterparty"] == "Globex"
        assert stored["riskLevel"] == "high"

    async def test_snapshots_the_pre_restore_state(self, client, db, contract, owner, users):
        """Rolling back is itself reversible: the current state is snapshotted first."""
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        response = await _restore(client, contract["id"], 1, users["admin"])

        assert response.status_code == 200
        new_version = response.json()["newVersion"]
        assert new_version == 2
        snapshot = await db.contract_versions.find_one(
            {"contractId": contract["id"], "version": new_version}
        )
        assert snapshot["title"] == contract["title"]
        assert snapshot["status"] == contract["status"]
        assert snapshot["changedBy"] == users["admin"]["id"]
        assert snapshot["changeReason"] == "Before restoring to version 1"

    async def test_versions_do_not_collide_across_repeated_restores(
        self, client, db, contract, owner, users
    ):
        """Two restores in a row allocate 2 then 3 -- no overwrite of history."""
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        first = await _restore(client, contract["id"], 1, users["admin"])
        second = await _restore(client, contract["id"], 1, users["admin"])

        assert first.json()["newVersion"] == 2
        assert second.json()["newVersion"] == 3
        assert await db.contract_versions.count_documents({"contractId": contract["id"]}) == 3

    async def test_response_reports_the_target_version(
        self, client, db, contract, owner, users
    ):
        await _seed(
            db,
            contract,
            make_version(contract["id"], 1, owner["id"]),
            make_version(contract["id"], 2, owner["id"]),
        )

        response = await _restore(client, contract["id"], 2, users["manager"])

        assert response.status_code == 200
        assert response.json()["message"] == "Contract restored to version 2"

    async def test_refreshes_updated_at(self, client, db, contract, owner, users):
        await _seed(db, contract, make_version(contract["id"], 1, owner["id"]))

        await _restore(client, contract["id"], 1, users["admin"])

        stored = await db.contracts.find_one({"id": contract["id"]})
        assert stored["updatedAt"] >= contract["updatedAt"]
