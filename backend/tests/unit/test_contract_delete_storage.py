"""Contract deletion must preserve the DB reference when storage is unavailable."""

from unittest.mock import AsyncMock, Mock

import pytest
from botocore.exceptions import EndpointConnectionError

from routes import contracts as contracts_route
from services import storage_service
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract, make_user


@pytest.fixture
async def stored_contract(db, org_id):
    """Seed a stored contract and chat history owned by an administrator."""
    admin = make_user(org_id, role="admin")
    await db.users.insert_one(dict(admin))
    contract = make_contract(org_id, admin["id"], storageKey="org/contract.pdf")
    await db.contracts.insert_one(dict(contract))
    await db.chat_history.insert_one(
        {"contractId": contract["id"], "message": "Keep me"}
    )
    return admin, contract


@pytest.mark.parametrize("failure", ["false", "exception", "endpoint"])
async def test_storage_failure_preserves_contract_and_chat(
    client, db, stored_contract, monkeypatch, failure, caplog
):
    """Keep the contract reference, chat, and audit unchanged after any storage failure."""
    admin, contract = stored_contract
    audit = AsyncMock()
    monkeypatch.setattr(contracts_route, "log_action", audit)
    if failure == "endpoint":
        # Exercise the real service translating a botocore failure into False.
        s3 = Mock()
        s3.delete_object.side_effect = EndpointConnectionError(
            endpoint_url="https://s3"
        )
        monkeypatch.setattr(storage_service, "get_s3_client", lambda: s3)
        monkeypatch.setattr(storage_service, "get_bucket_name", lambda: "test-bucket")
    else:
        delete = AsyncMock(return_value=False)
        if failure == "exception":
            delete.side_effect = RuntimeError("storage unavailable")
        monkeypatch.setattr(contracts_route, "delete_file_from_s3", delete)

    with caplog.at_level("ERROR"):
        response = await client.delete(
            f"{API_PREFIX}/contracts/{contract['id']}", headers=auth_headers(admin)
        )

    assert response.status_code == 502, response.text
    assert (
        response.json()["detail"] == "Failed to delete contract file. Please try again."
    )
    assert await db.contracts.find_one({"id": contract["id"]}) is not None
    assert await db.chat_history.count_documents({"contractId": contract["id"]}) == 1
    audit.assert_not_awaited()
    assert "Failed to delete contract file" in caplog.text


@pytest.mark.parametrize("storage", ["present", "already_absent", "no_key"])
async def test_successful_storage_delete_removes_contract_and_chat(
    client, db, stored_contract, monkeypatch, storage
):
    """Delete metadata only after storage confirms success or no file reference exists."""
    admin, contract = stored_contract
    audit = AsyncMock()
    monkeypatch.setattr(contracts_route, "log_action", audit)
    if storage == "already_absent":
        # S3 DeleteObject is idempotent: a missing key returns success (204).
        s3 = Mock()
        s3.delete_object.return_value = {"ResponseMetadata": {"HTTPStatusCode": 204}}
        monkeypatch.setattr(storage_service, "get_s3_client", lambda: s3)
        monkeypatch.setattr(storage_service, "get_bucket_name", lambda: "test-bucket")
    else:
        delete = AsyncMock(return_value=True)
        monkeypatch.setattr(contracts_route, "delete_file_from_s3", delete)
        if storage == "no_key":
            await db.contracts.update_one(
                {"id": contract["id"]}, {"$unset": {"storageKey": ""}}
            )

    response = await client.delete(
        f"{API_PREFIX}/contracts/{contract['id']}", headers=auth_headers(admin)
    )

    assert response.status_code == 200, response.text
    assert await db.contracts.find_one({"id": contract["id"]}) is None
    assert await db.chat_history.count_documents({"contractId": contract["id"]}) == 0
    audit.assert_awaited_once()
    if storage == "already_absent":
        s3.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key=contract["storageKey"]
        )
    elif storage == "no_key":
        delete.assert_not_awaited()
    else:
        delete.assert_awaited_once_with(contract["storageKey"])


async def test_missing_storage_configuration_preserves_contract(
    client, db, stored_contract, monkeypatch
):
    """An unconfigured storage client cannot confirm deletion of an existing stored file."""
    admin, contract = stored_contract
    monkeypatch.setattr(storage_service, "get_s3_client", lambda: None)
    response = await client.delete(
        f"{API_PREFIX}/contracts/{contract['id']}", headers=auth_headers(admin)
    )
    assert response.status_code == 502
    assert await db.contracts.find_one({"id": contract["id"]}) is not None
    assert await db.chat_history.count_documents({"contractId": contract["id"]}) == 1
