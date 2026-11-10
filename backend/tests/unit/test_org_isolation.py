"""Two-organization isolation: a member of org B can neither read nor change
org A's contracts, versions, tags, alerts, billing, team or intakes (404 where
the route already uses it), and every write on an org-owned record filters on
both its id and the caller's organizationId."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from routes import agentic as agentic_route
from routes import alerts as alerts_route
from routes import contracts as contracts_route
from routes import tags as tags_route
from routes import workflow as workflow_route
from services import workflow_service
from tests.unit.conftest import API_PREFIX, auth_headers, make_contract, make_user

ORG_A = "org-a"
ORG_B = "org-b"
NOW = datetime.now(timezone.utc)


def _iso(**delta):
    return (NOW + timedelta(**delta)).isoformat()


async def _writable(org_id):
    return None


@pytest.fixture
async def world(app, db, monkeypatch):
    """Org A owns one of everything; org B has an admin (the attacker) and its
    own contract and intake. Every route group under test is mounted."""
    from fastapi import APIRouter

    from routes import billing as billing_route

    tags_route.init_db(db)
    alerts_route.init_db(db)
    workflow_route.init_db(db)
    agentic_route.init_db(db)
    for module in (contracts_route, tags_route, alerts_route, workflow_route):
        if hasattr(module, "assert_org_writable"):
            monkeypatch.setattr(module, "assert_org_writable", _writable)
    api = APIRouter(prefix=API_PREFIX)
    api.include_router(tags_route.router)
    api.include_router(alerts_route.router)
    api.include_router(workflow_route.router)
    api.include_router(billing_route.router)
    api.include_router(agentic_route.build_router({"intake"}))
    app.include_router(api)

    for org in (ORG_A, ORG_B):
        await db.organization_billing.insert_one(
            {
                "id": f"b-{org}",
                "organizationId": org,
                "subscriptionTier": "trial",
                "billingStatus": "trialing",
                "trialEndDate": _iso(days=5),
                "trialContractsUsed": 0,
                "trialContractsLimit": 25,
                "stripeCustomerId": f"cus_{org}",
            }
        )
        await db.organizations.insert_one({"id": org, "name": org})

    a_admin = make_user(ORG_A, role="admin")
    a_member = make_user(ORG_A, role="user")
    b_admin = make_user(ORG_B, role="admin")
    await db.users.insert_many([dict(a_admin), dict(a_member), dict(b_admin)])

    a_contract = make_contract(
        ORG_A,
        a_member["id"],
        tags=["secret-a"],
        expiryDate=_iso(days=10),
        storageKey=f"{ORG_A}/msa.pdf",
        originalText="org A confidential text",
    )
    b_contract = make_contract(ORG_B, b_admin["id"], tags=["mine"])
    await db.contracts.insert_many([dict(a_contract), dict(b_contract)])
    await db.contract_versions.insert_one(
        {
            "id": "v-a-1",
            "contractId": a_contract["id"],
            "version": 1,
            "title": a_contract["title"],
            "status": "draft",
            "changedBy": a_member["id"],
            "changedAt": _iso(),
        }
    )
    await db.invitations.insert_one(
        {
            "id": "inv-a",
            "organizationId": ORG_A,
            "email": "new@a.test",
            "role": "user",
            "status": "pending",
            "expiresAt": _iso(days=3),
            "createdAt": _iso(),
            "invitedBy": a_admin["id"],
        }
    )
    await db.expiration_alerts.insert_one(
        {
            "id": "alert-a",
            "organizationId": ORG_A,
            "contractId": a_contract["id"],
            "status": "sent",
            "createdAt": _iso(),
        }
    )
    from models.agentic import ContractIntake, IntakeChannel

    await db.contract_intakes.insert_many(
        [
            ContractIntake(
                id=f"intake-{x}",
                organizationId=org,
                channel=IntakeChannel.EMAIL,
                source_email="x@example.com",
            ).model_dump()
            for x, org in (("a", ORG_A), ("b", ORG_B))
        ]
    )
    await db.legal_playbooks.insert_one(
        {"id": "pb-a", "organizationId": ORG_A, "name": "A playbook", "rules": []}
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield {
            "db": db,
            "client": client,
            "attacker": auth_headers(b_admin),
            "a_admin": a_admin,
            "a_member": a_member,
            "b_admin": b_admin,
            "a_contract": a_contract,
            "b_contract": b_contract,
        }


async def _a_contract(w):
    return await w["db"].contracts.find_one({"id": w["a_contract"]["id"]})


class TestContracts:
    """Org B cannot see or change org A's contracts or their versions."""

    async def test_reads_are_hidden(self, world):
        c, h, cid = world["client"], world["attacker"], world["a_contract"]["id"]
        listed = await c.get(f"{API_PREFIX}/contracts", headers=h)
        assert listed.status_code == 200
        assert cid not in {x["id"] for x in listed.json()}
        for path in ("", "/text", "/file", "/versions", "/versions/1"):
            r = await c.get(f"{API_PREFIX}/contracts/{cid}{path}", headers=h)
            assert r.status_code == 404, (path, r.status_code)
        history = await c.get(
            f"{API_PREFIX}/contracts/{cid}/workflow/history", headers=h
        )
        assert history.status_code == 404

    async def test_writes_are_rejected_and_change_nothing(self, world):
        c, h, cid = world["client"], world["attacker"], world["a_contract"]["id"]
        before = await _a_contract(world)
        attempts = [
            c.patch(f"{API_PREFIX}/contracts/{cid}", headers=h, json={"title": "x"}),
            c.post(f"{API_PREFIX}/contracts/{cid}/restore/1", headers=h),
            c.post(
                f"{API_PREFIX}/contracts/{cid}/workflow/submit_for_review", headers=h
            ),
            c.post(
                f"{API_PREFIX}/contracts/{cid}/chat",
                headers=h,
                json={"question": "What does it say?"},
            ),
            c.delete(f"{API_PREFIX}/contracts/{cid}", headers=h),
        ]
        for attempt in attempts:
            r = await attempt
            assert r.status_code == 404, (r.request.url, r.status_code)
        after = await _a_contract(world)
        assert after is not None
        assert {k: after[k] for k in ("title", "status", "updatedAt")} == {
            k: before[k] for k in ("title", "status", "updatedAt")
        }
        assert await world["db"].chat_history.count_documents({}) == 0


class TestTags:
    """Org B cannot list, add, remove or recategorise org A's tags."""

    async def test_tags_are_isolated(self, world):
        c, h, cid = world["client"], world["attacker"], world["a_contract"]["id"]
        listed = await c.get(f"{API_PREFIX}/tags", headers=h)
        assert "secret-a" not in listed.json()
        for r in (
            await c.post(f"{API_PREFIX}/tags/{cid}", headers=h, json=["pwned"]),
            await c.delete(f"{API_PREFIX}/tags/{cid}/secret-a", headers=h),
            await c.patch(
                f"{API_PREFIX}/tags/{cid}/category",
                headers=h,
                params={"category": "pwned"},
            ),
        ):
            assert r.status_code == 404
        after = await _a_contract(world)
        assert after["tags"] == ["secret-a"] and "category" not in after


class TestAlertsAndBilling:
    """Alert and billing reads only ever return the caller's organization."""

    async def test_alert_reads_are_scoped(self, world):
        c, h, cid = world["client"], world["attacker"], world["a_contract"]["id"]
        expiring = await c.get(f"{API_PREFIX}/alerts/expiring", headers=h)
        assert expiring.status_code == 200 and cid not in expiring.text
        history = await c.get(f"{API_PREFIX}/alerts/history", headers=h)
        assert history.status_code == 200 and "alert-a" not in history.text
        await c.put(
            f"{API_PREFIX}/alerts/settings",
            headers=h,
            json={"enabled": False, "daysBefore": [1]},
        )
        assert (
            await world["db"].alert_settings.count_documents({"organizationId": ORG_A})
            == 0
        )

    async def test_billing_reads_are_scoped(self, world):
        c, h = world["client"], world["attacker"]
        for path in ("/billing/subscription", "/billing/capabilities"):
            r = await c.get(f"{API_PREFIX}{path}", headers=h)
            assert r.status_code == 200, path
            assert f"cus_{ORG_A}" not in r.text and ORG_A not in r.text


class TestTeam:
    """Org B's admin cannot list, re-role or remove org A's members or invites."""

    async def test_team_is_isolated(self, world):
        c, h = world["client"], world["attacker"]
        member = world["a_member"]["id"]
        members = await c.get(f"{API_PREFIX}/team/members", headers=h)
        assert member not in {m["id"] for m in members.json()}
        invites = await c.get(f"{API_PREFIX}/team/invitations", headers=h)
        assert "inv-a" not in invites.text
        for r in (
            await c.patch(
                f"{API_PREFIX}/team/members/{member}/role",
                headers=h,
                json={"role": "admin"},
            ),
            await c.delete(f"{API_PREFIX}/team/members/{member}", headers=h),
            await c.delete(f"{API_PREFIX}/team/invitations/inv-a", headers=h),
        ):
            assert r.status_code == 404, (r.request.url, r.status_code)
        user = await world["db"].users.find_one({"id": member})
        assert user["role"] == "user" and user["organizationId"] == ORG_A
        invite = await world["db"].invitations.find_one({"id": "inv-a"})
        assert invite["status"] == "pending"


class TestIntake:
    """Org B cannot read or process org A's intakes, or point its own intake at
    org A's users or playbooks."""

    async def test_intakes_are_isolated(self, world):
        c, h = world["client"], world["attacker"]
        listed = await c.get(f"{API_PREFIX}/agentic/intake", headers=h)
        assert listed.status_code == 200 and "intake-a" not in listed.text
        assert (
            await c.get(f"{API_PREFIX}/agentic/intake/intake-a", headers=h)
        ).status_code == 404
        r = await c.post(
            f"{API_PREFIX}/agentic/intake/intake-a/process",
            headers=h,
            json={"intakeId": "intake-a", "assign_to": world["b_admin"]["id"]},
        )
        assert r.status_code == 404
        intake = await world["db"].contract_intakes.find_one({"id": "intake-a"})
        assert intake.get("assigned_to") is None

    @pytest.mark.parametrize(
        "body,detail",
        [
            ({"assign_to": "A_MEMBER"}, "Assignee not found"),
            ({"playbook_id": "pb-a"}, "Playbook not found"),
        ],
    )
    async def test_own_intake_cannot_reference_other_org(self, world, body, detail):
        c, h = world["client"], world["attacker"]
        body = {
            "intakeId": "intake-b",
            **{
                k: (world["a_member"]["id"] if v == "A_MEMBER" else v)
                for k, v in body.items()
            },
        }
        r = await c.post(
            f"{API_PREFIX}/agentic/intake/intake-b/process", headers=h, json=body
        )
        assert r.status_code == 404 and r.json()["detail"] == detail
        intake = await world["db"].contract_intakes.find_one({"id": "intake-b"})
        assert intake.get("assigned_to") is None and intake.get("playbook_id") is None

    async def test_own_intake_accepts_own_org_references(self, world):
        c, h = world["client"], world["attacker"]
        r = await c.post(
            f"{API_PREFIX}/agentic/intake/intake-b/process",
            headers=h,
            json={"intakeId": "intake-b", "assign_to": world["b_admin"]["id"]},
        )
        assert r.status_code == 200
        intake = await world["db"].contract_intakes.find_one({"id": "intake-b"})
        assert intake["assigned_to"] == world["b_admin"]["id"]


class TestWriteFilters:
    """Writes on org-owned records filter on both id and organizationId, so a
    record that changes hands between the lookup and the write is untouched."""

    @pytest.fixture
    def spy(self, world, monkeypatch):
        cls = type(world["db"].contracts)
        filters = []
        for method in ("update_one", "delete_one"):
            original = getattr(cls, method)

            def make(original):
                async def wrapper(self, flt, *args, **kwargs):
                    if self.name in ("contracts", "invitations", "contract_intakes"):
                        filters.append((self.name, dict(flt)))
                    return await original(self, flt, *args, **kwargs)

                return wrapper

            monkeypatch.setattr(cls, method, make(original))
        return filters

    async def test_own_org_writes_filter_on_org(self, world, spy):
        c = world["client"]
        h = auth_headers(world["b_admin"])
        cid = world["b_contract"]["id"]
        await c.patch(f"{API_PREFIX}/contracts/{cid}", headers=h, json={"title": "B2"})
        await c.post(f"{API_PREFIX}/tags/{cid}", headers=h, json=["t"])
        await c.delete(f"{API_PREFIX}/tags/{cid}/t", headers=h)
        await c.patch(
            f"{API_PREFIX}/tags/{cid}/category", headers=h, params={"category": "c"}
        )
        await c.post(
            f"{API_PREFIX}/contracts/{cid}/workflow/submit_for_review", headers=h
        )
        await c.delete(f"{API_PREFIX}/contracts/{cid}", headers=h)
        contract_writes = [f for name, f in spy if name == "contracts"]
        assert len(contract_writes) >= 6, spy
        for flt in contract_writes:
            assert flt.get("organizationId") == ORG_B and flt.get("id") == cid, flt

    async def test_status_transition_never_touches_another_org(self, world):
        a = await _a_contract(world)
        b_user = {
            "sub": world["b_admin"]["id"],
            "organizationId": ORG_B,
            "role": "admin",
            "email": world["b_admin"]["email"],
        }
        try:
            await workflow_service.apply_status_transition(
                world["db"], a, "review", b_user, action="submit_for_review"
            )
        except Exception:
            pass
        after = await _a_contract(world)
        assert after["status"] == a["status"]
