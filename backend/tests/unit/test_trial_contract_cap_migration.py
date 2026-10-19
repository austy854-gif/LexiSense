"""Migration 004 raises the trial contract cap from 3 to 25 on trial records only."""
from routes.billing import TRIAL_CONTRACT_LIMIT
from models.billing import OrganizationBilling, SubscriptionInfo
from utils.migrations import MIGRATIONS, _m004_raise_trial_contract_cap, run_migrations


async def _seed(db):
    await db.organization_billing.insert_many([
        {"organizationId": "trial-3", "subscriptionTier": "trial", "billingStatus": "trialing", "trialContractsLimit": 3},
        {"organizationId": "trial-custom", "subscriptionTier": "trial", "billingStatus": "trialing", "trialContractsLimit": 10},
        {"organizationId": "team-3", "subscriptionTier": "team", "billingStatus": "active", "trialContractsLimit": 3},
        {"organizationId": "cancelled-3", "subscriptionTier": "cancelled", "billingStatus": "cancelled", "trialContractsLimit": 3},
    ])


async def _limits(db):
    return {d["organizationId"]: d["trialContractsLimit"] async for d in db.organization_billing.find({})}


def test_defaults_are_25():
    assert TRIAL_CONTRACT_LIMIT == 25
    assert OrganizationBilling(organizationId="o").trialContractsLimit == 25
    assert SubscriptionInfo(subscriptionTier="trial", billingStatus="trialing").trialContractsLimit == 25


def test_registered_as_version_4():
    assert any(m["version"] == 4 and m["run"] is _m004_raise_trial_contract_cap for m in MIGRATIONS)


async def test_updates_trial_records_and_skips_paid(db):
    await _seed(db)
    await _m004_raise_trial_contract_cap(db)
    assert await _limits(db) == {"trial-3": 25, "trial-custom": 10, "team-3": 3, "cancelled-3": 3}


async def test_safe_to_rerun(db):
    await _seed(db)
    await _m004_raise_trial_contract_cap(db)
    await _m004_raise_trial_contract_cap(db)
    assert await _limits(db) == {"trial-3": 25, "trial-custom": 10, "team-3": 3, "cancelled-3": 3}


async def test_runner_applies_once(db):
    await _seed(db)
    await run_migrations(db)
    await db.organization_billing.insert_one(
        {"organizationId": "late-trial", "subscriptionTier": "trial", "trialContractsLimit": 3})
    await run_migrations(db)
    limits = await _limits(db)
    assert limits["trial-3"] == 25 and limits["team-3"] == 3
    assert limits["late-trial"] == 3  # recorded in schema_migrations; not re-applied
    assert await db.schema_migrations.count_documents({"version": 4}) == 1
