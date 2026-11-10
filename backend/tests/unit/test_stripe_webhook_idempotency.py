"""Stripe webhook: each event id is processed exactly once, older events never
overwrite newer subscription state, failures are retried, and the endpoint
fails closed on a bad signature or a missing secret. Stripe is mocked."""

import json
from datetime import datetime, timedelta, timezone

import pytest
import stripe

from routes import billing as billing_route
from tests.unit.conftest import API_PREFIX

URL = f"{API_PREFIX}/billing/webhook"
ORG = "org-stripe"


@pytest.fixture(autouse=True)
def fresh_index_cache():
    billing_route._stripe_event_index_ready.clear()
    yield
    billing_route._stripe_event_index_ready.clear()


@pytest.fixture
async def client(app):
    """Test app plus the billing router (conftest mounts contracts and team only)."""
    from fastapi import APIRouter
    from httpx import ASGITransport, AsyncClient

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(billing_route.router)
    app.include_router(api)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def stripe_env(app, db, monkeypatch):
    """Configured billing with signature checks mocked: the signature header
    must be ``good`` and the body is the event JSON."""
    monkeypatch.setattr(billing_route, "stripe_api_key", "sk_test_mock")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_mock")

    def construct_event(payload, sig_header, secret):
        assert secret == "whsec_mock"
        if sig_header != "good":
            raise stripe.SignatureVerificationError("bad signature", sig_header)
        return json.loads(payload)

    monkeypatch.setattr(stripe.Webhook, "construct_event", construct_event)
    await db.organization_billing.insert_one(
        {
            "id": "b-stripe",
            "organizationId": ORG,
            "subscriptionTier": "team",
            "billingStatus": "active",
            "stripeCustomerId": "cus_1",
            "stripeSubscriptionId": "sub_1",
        }
    )
    return db


def _sub_event(event_id, status, created, event_type="customer.subscription.updated"):
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "data": {
            "object": {
                "id": "sub_1",
                "status": status,
                "customer": "cus_1",
                "metadata": {"organizationId": ORG},
                "items": {"data": [{"price": {"id": "price_x"}}]},
            }
        },
    }


async def _post(client, event, sig="good"):
    return await client.post(
        URL,
        content=json.dumps(event),
        headers={"stripe-signature": sig, "content-type": "application/json"},
    )


async def _billing(db):
    return await db.organization_billing.find_one({"organizationId": ORG})


async def test_replayed_event_is_processed_once(client, stripe_env, monkeypatch):
    calls = []
    real = billing_route.update_billing_from_subscription

    async def counting(*args, **kwargs):
        calls.append(args[1]["status"])
        return await real(*args, **kwargs)

    monkeypatch.setattr(billing_route, "update_billing_from_subscription", counting)
    event = _sub_event("evt_1", "past_due", 1000)
    first = await _post(client, event)
    assert first.status_code == 200 and first.json() == {"received": True}
    replay = await _post(client, event)
    assert replay.status_code == 200
    assert replay.json() == {"received": True, "duplicate": True}
    assert calls == ["past_due"]
    ledger = await stripe_env.stripe_events.find_one({"eventId": "evt_1"})
    assert ledger["status"] == "done" and ledger["attempts"] == 1


async def test_older_event_does_not_overwrite_newer_state(client, stripe_env):
    newer = await _post(client, _sub_event("evt_new", "canceled", 2000))
    assert newer.status_code == 200
    older = await _post(client, _sub_event("evt_old", "active", 1000))
    assert older.status_code == 200
    doc = await _billing(stripe_env)
    assert doc["billingStatus"] == "cancelled"
    assert doc["lastStripeEventCreated"] == 2000
    # The skipped event is still recorded as handled, so it is not retried.
    ledger = await stripe_env.stripe_events.find_one({"eventId": "evt_old"})
    assert ledger["status"] == "done"


async def test_older_invoice_event_cannot_reopen_past_due(client, stripe_env):
    await _post(client, _sub_event("evt_paid_sub", "active", 3000))
    invoice = {
        "id": "evt_inv_old",
        "type": "invoice.payment_failed",
        "created": 2500,
        "data": {"object": {"customer": "cus_1", "subscription": "sub_1"}},
    }
    assert (await _post(client, invoice)).status_code == 200
    assert (await _billing(stripe_env))["billingStatus"] == "active"


async def test_failed_event_is_retried_on_redelivery(client, stripe_env, monkeypatch):
    real = billing_route.update_billing_from_subscription
    attempts = []

    async def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("mongo blip")
        return await real(*args, **kwargs)

    monkeypatch.setattr(billing_route, "update_billing_from_subscription", flaky)
    event = _sub_event("evt_retry", "past_due", 1000)
    failed = await _post(client, event)
    assert failed.status_code == 500
    assert "mongo blip" not in failed.text
    ledger = await stripe_env.stripe_events.find_one({"eventId": "evt_retry"})
    assert ledger["status"] == "failed" and ledger["error"] == "RuntimeError"
    assert (await _billing(stripe_env))["billingStatus"] == "active"

    retried = await _post(client, event)
    assert retried.status_code == 200
    assert (await _billing(stripe_env))["billingStatus"] == "past_due"
    ledger = await stripe_env.stripe_events.find_one({"eventId": "evt_retry"})
    assert ledger["status"] == "done" and ledger["attempts"] == 2


async def test_live_claim_is_busy_and_crashed_claim_is_reclaimed(client, stripe_env):
    now = datetime.now(timezone.utc)
    await stripe_env.stripe_events.insert_many(
        [
            {
                "eventId": "evt_live",
                "status": "processing",
                "claimToken": "t1",
                "claimedAt": now.isoformat(),
                "attempts": 1,
            },
            {
                "eventId": "evt_crashed",
                "status": "processing",
                "claimToken": "t2",
                "claimedAt": (
                    now - billing_route.STRIPE_EVENT_LEASE - timedelta(seconds=1)
                ).isoformat(),
                "attempts": 1,
            },
        ]
    )
    busy = await _post(client, _sub_event("evt_live", "past_due", 1000))
    assert busy.status_code == 409
    assert (await _billing(stripe_env))["billingStatus"] == "active"

    reclaimed = await _post(client, _sub_event("evt_crashed", "past_due", 1000))
    assert reclaimed.status_code == 200
    assert (await _billing(stripe_env))["billingStatus"] == "past_due"
    ledger = await stripe_env.stripe_events.find_one({"eventId": "evt_crashed"})
    assert ledger["status"] == "done" and ledger["claimToken"] != "t2"


async def test_bad_signature_is_rejected_without_processing(client, stripe_env):
    r = await _post(client, _sub_event("evt_bad", "canceled", 1000), sig="forged")
    assert r.status_code == 400
    assert await stripe_env.stripe_events.count_documents({}) == 0
    assert (await _billing(stripe_env))["billingStatus"] == "active"


async def test_missing_webhook_secret_fails_closed(client, stripe_env, monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    r = await _post(client, _sub_event("evt_nosecret", "canceled", 1000))
    assert r.status_code == 500
    assert await stripe_env.stripe_events.count_documents({}) == 0
    assert (await _billing(stripe_env))["billingStatus"] == "active"


async def test_unconfirmed_event_index_fails_closed(client, stripe_env, monkeypatch):
    async def index_down(database):
        raise RuntimeError("createIndexes failed")

    monkeypatch.setattr(billing_route, "ensure_stripe_event_index", index_down)
    r = await _post(client, _sub_event("evt_noindex", "canceled", 1000))
    assert r.status_code == 503
    assert (await _billing(stripe_env))["billingStatus"] == "active"


async def test_concurrent_first_deliveries_claim_once(stripe_env):
    import asyncio

    await billing_route.ensure_stripe_event_index(stripe_env)
    outcomes = await asyncio.gather(
        *[billing_route._claim_stripe_event("evt_race", "x", 1) for _ in range(5)]
    )
    claimed = [o for o, _ in outcomes if o == billing_route.EVENT_CLAIMED]
    assert len(claimed) == 1


@pytest.mark.parametrize("created", [None, "1000", 1.5, True])
async def test_event_without_valid_created_time_is_rejected(
    client, stripe_env, created
):
    """Ordering needs an authoritative event time: reject before claiming."""
    event = _sub_event("evt_no_time", "canceled", 1000)
    if created is None:
        event.pop("created")
    else:
        event["created"] = created
    r = await _post(client, event)
    assert r.status_code == 400
    assert await stripe_env.stripe_events.count_documents({}) == 0
    assert (await _billing(stripe_env))["billingStatus"] == "active"


def _invoice_event(event_id, event_type, created):
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "data": {"object": {"customer": "cus_1", "subscription": "sub_1"}},
    }


async def test_paid_invoice_blocks_an_older_failure_while_active(client, stripe_env):
    """invoice.paid on an active account still records its time, so an older
    payment failure delivered later cannot move it to past due."""
    assert (
        await _post(client, _invoice_event("evt_paid", "invoice.paid", 3000))
    ).status_code == 200
    doc = await _billing(stripe_env)
    assert doc["billingStatus"] == "active" and doc["lastStripeEventCreated"] == 3000
    late = _invoice_event("evt_fail_old", "invoice.payment_failed", 2500)
    assert (await _post(client, late)).status_code == 200
    assert (await _billing(stripe_env))["billingStatus"] == "active"


async def test_unconfirmed_settlement_is_not_acknowledged_and_replay_is_a_noop(
    client, stripe_env, monkeypatch
):
    """If the done write fails the webhook answers 500; when Stripe redelivers
    after the lease, the already-applied billing write is not repeated, so a
    same-second invoice payment applied in between is not overwritten."""
    real_settle = billing_route._settle_stripe_event
    fail_once = {"evt_fail": True}

    async def flaky_settle(event_id, token, fields):
        if fields.get("status") == "done" and fail_once.pop(event_id, False):
            return False
        return await real_settle(event_id, token, fields)

    monkeypatch.setattr(billing_route, "_settle_stripe_event", flaky_settle)
    failure = _invoice_event("evt_fail", "invoice.payment_failed", 1000)
    first = await _post(client, failure)
    assert first.status_code == 500
    assert (await _billing(stripe_env))["billingStatus"] == "past_due"

    paid = _invoice_event("evt_paid_same_second", "invoice.paid", 1000)
    assert (await _post(client, paid)).status_code == 200
    assert (await _billing(stripe_env))["billingStatus"] == "active"

    # The failed settlement's claim expires; Stripe redelivers the failure.
    await stripe_env.stripe_events.update_one(
        {"eventId": "evt_fail"},
        {
            "$set": {
                "claimedAt": (
                    datetime.now(timezone.utc)
                    - billing_route.STRIPE_EVENT_LEASE
                    - timedelta(seconds=1)
                ).isoformat()
            }
        },
    )
    replay = await _post(client, failure)
    assert replay.status_code == 200
    doc = await _billing(stripe_env)
    assert doc["billingStatus"] == "active"
    assert doc["appliedStripeEventIds"].count("evt_fail") == 1
    ledger = await stripe_env.stripe_events.find_one({"eventId": "evt_fail"})
    assert ledger["status"] == "done"
