"""Tag routes follow the contract edit policy: admins/managers on any org
contract, a ``user`` only on contracts they uploaded, viewers never."""

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from routes import tags as tags_route
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract, make_user


@pytest.fixture
async def tag_client(db, monkeypatch):
    async def _writable(org):
        return None

    monkeypatch.setattr(tags_route, "db", db)
    monkeypatch.setattr(tags_route, "assert_org_writable", _writable)
    app = FastAPI()
    api = APIRouter(prefix=API_PREFIX)
    api.include_router(tags_route.router)
    app.include_router(api)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c


@pytest.mark.parametrize(
    "role,owns,expected",
    [
        ("user", True, 200),
        ("user", False, 403),
        ("viewer", True, 403),
        ("manager", False, 200),
        ("admin", False, 200),
    ],
)
async def test_tag_write_permissions(tag_client, db, org_id, role, owns, expected):
    actor = make_user(org_id, role=role)
    other = make_user(org_id, role="manager")
    contract = make_contract(org_id, actor["id"] if owns else other["id"])
    await db.contracts.insert_one(dict(contract))
    url = f"{API_PREFIX}/tags/{contract['id']}"
    headers = auth_headers(actor)

    added = await tag_client.post(url, json=["nda"], headers=headers)
    assert added.status_code == expected
    removed = await tag_client.delete(f"{url}/nda", headers=headers)
    assert removed.status_code == expected
    category = await tag_client.patch(
        f"{url}/category", params={"category": "Legal"}, headers=headers
    )
    assert category.status_code == expected


async def test_other_org_contract_is_not_found(tag_client, db, org_id, other_org_id):
    actor = make_user(org_id, role="admin")
    contract = make_contract(other_org_id, "someone")
    await db.contracts.insert_one(dict(contract))
    r = await tag_client.post(
        f"{API_PREFIX}/tags/{contract['id']}", json=["nda"], headers=auth_headers(actor)
    )
    assert r.status_code == 404
