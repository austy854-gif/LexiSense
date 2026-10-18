"""Entitlement gate on background AI paths (Celery agent runs, the scheduled
agent sweep, playbook analysis and automatic playbook analysis on email
intake). Lapsed orgs are skipped -- no AI work, a log line and a recorded
``skipped`` status -- instead of crashing."""
import logging

import pytest

from models.agentic import ContractIntake, IntakeChannel
from models.billing import BillingStatus, SubscriptionTier
from services import agent_tasks, audit_service, intake_tasks, playbook_tasks
from services import obligation_tasks, risk_tasks
from services.task_db import current_db
from tests.unit.conftest import iso_in, make_contract

ORG = "org-bg"

ENTITLED = {
    "trial": dict(subscriptionTier=SubscriptionTier.TRIAL, billingStatus=BillingStatus.TRIALING, trialEndDate=iso_in(3)),
    "paid": dict(subscriptionTier=SubscriptionTier.TEAM, billingStatus=BillingStatus.ACTIVE),
    "grace": dict(subscriptionTier=SubscriptionTier.TEAM, billingStatus=BillingStatus.PAST_DUE, pastDueSince=iso_in(-2)),
}
BLOCKED = {
    "expired": dict(subscriptionTier=SubscriptionTier.TRIAL, billingStatus=BillingStatus.TRIALING, trialEndDate=iso_in(-1)),
    "cancelled": dict(subscriptionTier=SubscriptionTier.CANCELLED, billingStatus=BillingStatus.CANCELLED),
    "past_due": dict(subscriptionTier=SubscriptionTier.TEAM, billingStatus=BillingStatus.PAST_DUE, pastDueSince=iso_in(-9)),
}


@pytest.fixture
def task_db(db):
    audit_service.init_db(db)
    token = current_db.set(db)
    yield db
    current_db.reset(token)


@pytest.fixture
def delays(monkeypatch):
    calls = []

    def recorder(name):
        return lambda *a, **k: calls.append((name, a, k))

    monkeypatch.setattr(agent_tasks.execute_contract_agent, "delay", recorder("agent"))
    monkeypatch.setattr(playbook_tasks.analyze_contract_with_playbook, "delay", recorder("playbook"))
    monkeypatch.setattr(risk_tasks.assess_contract_risk, "delay", recorder("risk"))
    monkeypatch.setattr(obligation_tasks.extract_obligations_from_contract, "delay", recorder("obligations"))
    return calls


async def _billing(db, state, org=ORG):
    await db.organization_billing.insert_one({"id": f"b-{org}", "organizationId": org, **state})


async def _agent(db, agent_id="ag-1", org=ORG):
    await db.contract_agents.insert_one({
        "id": agent_id, "organizationId": org, "createdBy": "u-1", "name": "Expiry",
        "agentType": "expiration_monitor", "status": "active", "config": {}, "executionHistory": [],
    })


def test_task_modules_use_the_per_task_db(db):
    token = current_db.set(db)
    try:
        assert agent_tasks.get_db() is db and playbook_tasks.get_db() is db
    finally:
        current_db.reset(token)


@pytest.mark.parametrize("name", list(BLOCKED))
async def test_agent_run_skipped_without_entitlement(task_db, name, caplog):
    await _billing(task_db, BLOCKED[name])
    await _agent(task_db)
    with caplog.at_level(logging.INFO, logger="routes.billing"):
        result = await agent_tasks._execute_contract_agent_async("ag-1")
    assert result["status"] == "skipped"
    agent = await task_db.contract_agents.find_one({"id": "ag-1"})
    (log,) = agent["executionHistory"]
    assert log["status"] == "skipped" and log["skip_reason"]
    assert agent.get("totalExecutions", 0) == 0
    assert "Skipping agent run ag-1" in caplog.text


@pytest.mark.parametrize("name", list(ENTITLED))
async def test_agent_run_executes_with_entitlement(task_db, name):
    await _billing(task_db, ENTITLED[name])
    await _agent(task_db)
    result = await agent_tasks._execute_contract_agent_async("ag-1")
    assert result["status"] == "success"
    agent = await task_db.contract_agents.find_one({"id": "ag-1"})
    assert agent["executionHistory"][0]["status"] == "completed"


async def test_scheduled_sweep_only_queues_entitled_orgs(task_db, delays):
    await _billing(task_db, ENTITLED["paid"], org="org-paid")
    await _billing(task_db, BLOCKED["expired"], org="org-expired")
    await _agent(task_db, "ag-paid", org="org-paid")
    await _agent(task_db, "ag-expired", org="org-expired")
    result = await agent_tasks._check_due_agents_async()
    assert (result["agents_queued"], result["agents_skipped"]) == (1, 1)
    assert [c[1] for c in delays] == [("ag-paid",)]
    skipped = await task_db.contract_agents.find_one({"id": "ag-expired"})
    assert skipped["executionHistory"][0]["status"] == "skipped"
    assert skipped["nextRunAt"]  # still rescheduled, so it isn't retried every hour
    paid = await task_db.contract_agents.find_one({"id": "ag-paid"})
    assert paid["executionHistory"] == []


async def _seed_playbook_contract(db, org=ORG):
    await db.contracts.insert_one(make_contract(org, "u-1", contract_id="c-pb", originalText="Confidentiality applies."))
    await db.legal_playbooks.insert_one({"id": "pb-1", "organizationId": org, "rules": []})


@pytest.mark.parametrize("name", list(BLOCKED))
async def test_playbook_task_skipped_without_entitlement(task_db, name):
    await _billing(task_db, BLOCKED[name])
    await _seed_playbook_contract(task_db)
    result = await playbook_tasks._analyze_contract_with_playbook_async("c-pb", "pb-1")
    assert result["status"] == "skipped"
    assert await task_db.redline_sessions.count_documents({}) == 0


async def test_playbook_task_runs_with_entitlement(task_db):
    await _billing(task_db, ENTITLED["paid"])
    await _seed_playbook_contract(task_db)
    result = await playbook_tasks._analyze_contract_with_playbook_async("c-pb", "pb-1")
    assert result["status"] == "success"


async def test_playbook_task_rejects_other_orgs_playbook(task_db):
    await _billing(task_db, ENTITLED["paid"])
    await _seed_playbook_contract(task_db)
    await task_db.legal_playbooks.insert_one({"id": "pb-other", "organizationId": "org-x", "rules": []})
    result = await playbook_tasks._analyze_contract_with_playbook_async("c-pb", "pb-other")
    assert result["status"] == "error"


def _intake(playbook_id="pb-1"):
    return ContractIntake(
        organizationId=ORG, channel=IntakeChannel.EMAIL, source_email="a@b.test",
        attachments=[{"filename": "nda.pdf", "size": 10, "mime_type": "application/pdf", "storage_key": "k"}],
        playbook_id=playbook_id,
    )


@pytest.mark.parametrize("name", list(BLOCKED))
async def test_intake_skips_auto_playbook_without_entitlement(task_db, delays, name):
    await _billing(task_db, BLOCKED[name])
    intake = _intake()
    await task_db.contract_intakes.insert_one(intake.model_dump())
    contract_id = await intake_tasks._create_contract_from_intake(intake, {"contract_type": "NDA"}, task_db)
    assert contract_id  # the contract itself is still created
    assert "playbook" not in [c[0] for c in delays]
    doc = await task_db.contract_intakes.find_one({"id": intake.id})
    assert doc["playbook_analysis_status"] == "skipped"


async def test_intake_queues_auto_playbook_with_entitlement(task_db, delays):
    await _billing(task_db, ENTITLED["trial"])
    intake = _intake()
    await task_db.contract_intakes.insert_one(intake.model_dump())
    contract_id = await intake_tasks._create_contract_from_intake(intake, {"contract_type": "NDA"}, task_db)
    assert ("playbook", (contract_id, "pb-1"), {"auto_apply": False}) in delays
    doc = await task_db.contract_intakes.find_one({"id": intake.id})
    assert doc["playbook_analysis_status"] == "queued"


async def test_missing_billing_record_starts_trial(task_db):
    from routes.billing import background_ai_entitlement

    ent = await background_ai_entitlement("org-new", task_db, "test")
    assert ent is not None and ent.state == "trial"
    assert await task_db.organization_billing.count_documents({"organizationId": "org-new"}) == 1
    assert await background_ai_entitlement(None, task_db, "test") is None
