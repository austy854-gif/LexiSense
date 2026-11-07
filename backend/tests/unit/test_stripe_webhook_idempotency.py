"""Real webhook and Mongo regressions; Stripe is always stubbed."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from routes import billing as billing
from utils.database import apply_indexes
from tests.unit.test_pricing_integrity import _checkout, fake_stripe, price_env  # noqa: F401


def subscription(state="active", sid="sub_1"):
    return {"id": sid, "customer": "cus_1", "metadata": {"organizationId": "org-alpha"},
            "status": state, "items": {"data": [{"price": {"id": "price_team_monthly"}}]}}


def event(kind="customer.subscription.updated", eid="evt_1", data=None):
    return {"id": eid, "type": kind, "data": {"object": data or subscription()}}


@pytest.fixture
async def webhook(db, monkeypatch):
    billing.init_db(db)
    monkeypatch.setattr(billing, "stripe_api_key", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    await apply_indexes(db)
    app = FastAPI()
    app.include_router(billing.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def send(evt):
            monkeypatch.setattr(billing.stripe.Webhook, "construct_event", lambda *a: evt)
            return await client.post("/billing/webhook", content=b"signed", headers={"stripe-signature": "test"})
        yield send


async def seed(db):
    await db.organization_billing.insert_one({"organizationId": "org-alpha", "stripeCustomerId": "cus_1",
        "stripeSubscriptionId": "sub_1", "subscriptionTier": "team", "billingStatus": "active"})


async def test_replay_is_one_state_change(webhook, db, monkeypatch):
    await seed(db)
    retrieve = AsyncMock(return_value=subscription("past_due"))
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", retrieve)
    assert (await webhook(event())).status_code == 200
    before = await db.organization_billing.find_one({})
    second = await webhook(event())
    assert second.status_code == 200 and second.json() == {"received": True, "duplicate": True}
    assert await db.organization_billing.find_one({}) == before
    assert before["billingStatus"] == "past_due"
    retrieve.assert_awaited_once_with("sub_1")
    claim = await db.stripe_events.find_one({"event_id": "evt_1"})
    assert claim["type"] == "customer.subscription.updated" and isinstance(claim["created_at"], datetime)


async def test_stale_update_after_delete_stays_cancelled(webhook, db, monkeypatch):
    await seed(db)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", AsyncMock(side_effect=lambda sid: subscription("canceled")))
    assert (await webhook(event("customer.subscription.deleted", "evt_delete", subscription("canceled")))).status_code == 200
    assert (await webhook(event(eid="evt_old_update"))).status_code == 200
    doc = await db.organization_billing.find_one({})
    assert doc["billingStatus"] == "cancelled" and doc["subscriptionTier"] == "cancelled"


async def test_failed_dispatch_releases_claim(webhook, db, monkeypatch):
    await seed(db)
    retrieve = AsyncMock(side_effect=[RuntimeError("temporary Stripe failure"), subscription("past_due")])
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", retrieve)
    with pytest.raises(RuntimeError, match="temporary Stripe failure"):
        await webhook(event())
    assert await db.stripe_events.count_documents({}) == 0
    assert (await webhook(event())).json() == {"received": True}
    assert await db.stripe_events.count_documents({}) == 1 and retrieve.call_count == 2


@pytest.mark.parametrize("kind", ["customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"])
async def test_subscription_handlers_use_current_state(webhook, db, monkeypatch, kind):
    await seed(db)
    retrieve = AsyncMock(return_value=subscription())
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", retrieve)
    assert (await webhook(event(kind, data=subscription("canceled")))).status_code == 200
    assert (await db.organization_billing.find_one({}))["billingStatus"] == "active"
    retrieve.assert_awaited_once_with("sub_1")


@pytest.mark.parametrize("kind", ["customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted", "invoice.paid", "invoice.payment_failed", "checkout.session.completed"])
async def test_missing_subscription_is_terminal(webhook, db, monkeypatch, kind):
    await seed(db)
    error = billing.stripe.InvalidRequestError("No such subscription", param="id", code="resource_missing", http_status=404)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", AsyncMock(side_effect=error))
    data = subscription()
    if not kind.startswith("customer."):
        data = {"subscription": "sub_1", "customer": "cus_1", "metadata": {"organizationId": "org-alpha"}}
    assert (await webhook(event(kind, data=data))).status_code == 200
    assert (await db.organization_billing.find_one({}))["billingStatus"] == "cancelled"


@pytest.mark.parametrize("kind", ["invoice.paid", "invoice.payment_failed"])
@pytest.mark.parametrize("state", ["active", "past_due", "canceled"])
async def test_invoice_reconciles_fresh_subscription(webhook, db, monkeypatch, kind, state):
    await seed(db)
    retrieve = AsyncMock(return_value=subscription(state))
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", retrieve)
    data = {"customer": "cus_1", "parent": {"subscription_details": {"subscription": "sub_1"}}}
    assert (await webhook(event(kind, data=data))).status_code == 200
    assert (await db.organization_billing.find_one({}))["billingStatus"] == ("cancelled" if state == "canceled" else state)
    retrieve.assert_awaited_once_with("sub_1")


@pytest.mark.parametrize("kind", ["invoice.paid", "invoice.payment_failed"])
async def test_non_subscription_invoice_is_noop(webhook, db, monkeypatch, kind):
    await seed(db)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", AsyncMock(side_effect=AssertionError("must not retrieve")))
    before = await db.organization_billing.find_one({})
    await webhook(event(kind, data={"customer": "cus_1"}))
    assert await db.organization_billing.find_one({}) == before


async def test_invalid_signature_never_claimed(webhook, db, monkeypatch):
    def invalid(*args):
        raise billing.stripe.SignatureVerificationError("invalid", "bad")
    monkeypatch.setattr(billing.stripe.Webhook, "construct_event", invalid)
    request = SimpleNamespace(body=AsyncMock(return_value=b"bad"), headers={"stripe-signature": "bad"})
    with pytest.raises(HTTPException) as exc:
        await billing.stripe_webhook(request)
    assert exc.value.status_code == 400
    assert await db.stripe_events.count_documents({}) == 0


async def test_indexes_include_unique_and_real_ttl(db):
    await apply_indexes(db)
    await apply_indexes(db)
    indexes = await db.stripe_events.index_information()
    assert indexes["uq_stripe_events_event_id"]["unique"] is True
    assert indexes["ttl_stripe_events_created_at"]["expireAfterSeconds"] == 30 * 24 * 60 * 60


async def test_checkout_has_stable_org_price_bucket_key(app, db, fake_stripe, monkeypatch, clock):
    monkeypatch.setattr(billing.stripe.Subscription, "list", lambda **kw: {"data": []})
    await _checkout("org-alpha", planId="team")
    await _checkout("org-alpha", planId="team")
    first, second = fake_stripe.sessions
    assert first["idempotency_key"] == second["idempotency_key"]
    await _checkout("org-beta", planId="team")
    await _checkout("org-alpha", planId="starter")
    assert len({c["idempotency_key"] for c in fake_stripe.sessions}) == 3


async def test_webhook_indexes_fail_closed(db, monkeypatch):
    from pymongo.errors import OperationFailure
    collection_type = type(db.stripe_events)
    original = collection_type.create_index

    async def fail_stripe(self, *args, **kwargs):
        if kwargs.get("name") == "uq_stripe_events_event_id":
            raise OperationFailure("index unavailable")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(collection_type, "create_index", fail_stripe)
    with pytest.raises(OperationFailure, match="index unavailable"):
        await apply_indexes(db)


async def test_checkout_key_changes_with_bucket(monkeypatch):
    from datetime import datetime, timezone
    class Clock(datetime):
        timestamp = 1200

        @classmethod
        def now(cls, tz):
            return datetime.fromtimestamp(cls.timestamp, timezone.utc)

    monkeypatch.setattr(billing, "datetime", Clock)
    first = billing.checkout_idempotency_key("org", "price")
    Clock.timestamp = 1799
    assert billing.checkout_idempotency_key("org", "price") == first
    Clock.timestamp = 1800
    assert billing.checkout_idempotency_key("org", "price") != first


@pytest.mark.parametrize("kind", ["customer.subscription.updated", "invoice.payment_failed"])
async def test_non_missing_stripe_error_is_retryable(webhook, db, monkeypatch, kind):
    await seed(db)
    error = billing.stripe.InvalidRequestError("bad request", param="id", http_status=400)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", AsyncMock(side_effect=error))
    data = subscription() if kind.startswith("customer.") else {"subscription": "sub_1", "customer": "cus_1"}
    with pytest.raises(billing.stripe.InvalidRequestError):
        await webhook(event(kind, data=data))
    assert await db.stripe_events.count_documents({}) == 0
    assert (await db.organization_billing.find_one({}))["billingStatus"] == "active"


@pytest.mark.parametrize("kind", ["customer.subscription.deleted", "invoice.paid", "invoice.payment_failed"])
async def test_dead_foreign_subscription_cannot_cancel_current(webhook, db, monkeypatch, kind):
    await seed(db)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", AsyncMock(side_effect=lambda sid: subscription("canceled", sid)))
    data = subscription("canceled", "sub_other") if kind.startswith("customer.") else {"subscription": "sub_other", "customer": "cus_1"}
    await webhook(event(kind, data=data))
    assert (await db.organization_billing.find_one({}))["billingStatus"] == "active"


async def test_webhook_waits_for_bootstrap_indexes(db, monkeypatch):
    billing.init_db(db)
    monkeypatch.setattr(billing, "stripe_api_key", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(billing.stripe.Webhook, "construct_event", lambda *a: event())
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", AsyncMock(side_effect=lambda sid: subscription()))
    app = FastAPI()
    app.state.bootstrap_ok = False
    app.include_router(billing.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/billing/webhook", content=b"signed")
    assert response.status_code == 503
    assert await db.stripe_events.count_documents({}) == 0


async def test_cancellation_timestamp_survives_late_updates(webhook, db, monkeypatch):
    await seed(db)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", AsyncMock(side_effect=lambda sid: subscription("canceled")))
    await webhook(event("customer.subscription.deleted"))
    first = await db.organization_billing.find_one({})
    assert first.get("cancelledAt")
    await webhook(event(eid="evt_late"))
    assert (await db.organization_billing.find_one({}))["cancelledAt"] == first["cancelledAt"]


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        current = datetime.now(timezone.utc)

        @classmethod
        def now(cls, tz):
            return cls.current.astimezone(tz)

        @classmethod
        def advance(cls, seconds):
            cls.current += timedelta(seconds=seconds)

    monkeypatch.setattr(billing, "datetime", Clock)
    return Clock


async def test_abandoned_claim_is_reclaimed_after_lease(webhook, db, monkeypatch, clock):
    await db.stripe_events.insert_one({"event_id": "evt_1", "type": event()["type"],
        "created_at": clock.current, "status": "processing", "owner_token": "abandoned",
        "lease_until": clock.current + timedelta(seconds=60)})
    dispatch = AsyncMock()
    monkeypatch.setattr(billing, "dispatch_stripe_event", dispatch)
    assert (await webhook(event())).status_code == 503
    clock.advance(61)
    assert (await webhook(event())).status_code == 200
    doc = await db.stripe_events.find_one({})
    assert doc["status"] == "done" and doc["owner_token"] != "abandoned"
    assert doc["created_at"].replace(tzinfo=timezone.utc) == clock.current.replace(microsecond=(clock.current.microsecond // 1000) * 1000)
    assert "lease_until" not in doc
    dispatch.assert_awaited_once()


async def test_processing_duplicate_retries_after_first_failure(webhook, db, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def dispatch(evt):
        calls.append(evt["id"])
        if len(calls) == 1:
            entered.set()
            await release.wait()
            raise RuntimeError("first dispatch failed")

    monkeypatch.setattr(billing, "dispatch_stripe_event", dispatch)
    first = asyncio.create_task(webhook(event()))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        duplicate = await webhook(event())
        assert duplicate.status_code == 503
        claim = await db.stripe_events.find_one({})
        assert claim["status"] == "processing" and claim["owner_token"] and claim["lease_until"]
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="first dispatch failed"):
            await first
    assert (await webhook(event())).status_code == 200
    assert len(calls) == 2
    assert (await db.stripe_events.find_one({}))["status"] == "done"


async def test_release_db_failure_recovers_via_lease(webhook, db, monkeypatch, clock):
    from pymongo.errors import AutoReconnect
    collection_type = type(db.stripe_events)
    original = collection_type.delete_one
    failures = []

    async def fail_once(self, *args, **kwargs):
        if self.name == "stripe_events" and not failures:
            failures.append(True)
            raise AutoReconnect("claim release unavailable")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(collection_type, "delete_one", fail_once)
    dispatch = AsyncMock(side_effect=[RuntimeError("dispatch failed"), None])
    monkeypatch.setattr(billing, "dispatch_stripe_event", dispatch)
    with pytest.raises(RuntimeError, match="dispatch failed"):
        await webhook(event())
    assert (await db.stripe_events.find_one({}))["status"] == "processing"
    assert (await webhook(event())).status_code == 503
    clock.advance(61)
    assert (await webhook(event())).status_code == 200
    assert (await db.stripe_events.find_one({}))["status"] == "done"
    assert dispatch.await_count == 2


@pytest.mark.parametrize("first_fails", [False, True])
async def test_expired_owner_cannot_complete_or_delete_new_claim(webhook, db, monkeypatch, clock, first_fails):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def dispatch(evt):
        calls.append(evt["id"])
        if len(calls) == 1:
            entered.set()
            await release.wait()
            if first_fails:
                raise RuntimeError("old owner failed")

    monkeypatch.setattr(billing, "dispatch_stripe_event", dispatch)
    first = asyncio.create_task(webhook(event()))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        old = await db.stripe_events.find_one({})
        clock.advance(61)
        assert (await webhook(event())).status_code == 200
        done = await db.stripe_events.find_one({})
        assert done["status"] == "done" and done["owner_token"] != old["owner_token"]
    finally:
        release.set()
        if first_fails:
            with pytest.raises(RuntimeError, match="old owner failed"):
                await first
        else:
            assert (await first).status_code == 503
    assert await db.stripe_events.find_one({}) == done


async def test_subscription_retrieval_uses_async_sdk(monkeypatch):
    sync = Mock(side_effect=AssertionError("synchronous Stripe I/O on event loop"))
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve", sync)
    yielded = asyncio.Event()

    async def async_retrieve(sid):
        await asyncio.sleep(0)
        yielded.set()
        return subscription()

    sdk = AsyncMock(side_effect=async_retrieve)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", sdk)
    assert await billing.retrieve_current_subscription("sub_1") == subscription()
    assert yielded.is_set()
    sdk.assert_awaited_once_with("sub_1")
    sync.assert_not_called()


async def test_async_stripe_timeout_releases_claim(webhook, db, monkeypatch):
    await seed(db)
    monkeypatch.setattr(billing, "STRIPE_RETRIEVE_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve", Mock(side_effect=AssertionError("sync call")))

    async def hang(sid):
        await asyncio.Event().wait()

    sdk = AsyncMock(side_effect=hang)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", sdk)
    with pytest.raises(TimeoutError):
        await webhook(event())
    assert await db.stripe_events.count_documents({}) == 0
    sdk.assert_awaited_once_with("sub_1")


@pytest.mark.parametrize("kind", ["customer.subscription.updated", "customer.subscription.created",
    "checkout.session.completed", "invoice.paid", "invoice.payment_failed"])
async def test_concurrent_stale_retrieval_cannot_overwrite_cancellation(webhook, db, monkeypatch, kind):
    await seed(db)  # Legacy billing document deliberately has no revision counter.
    retrieved_active, resume = asyncio.Event(), asyncio.Event()
    calls = []

    async def retrieve(sid):
        calls.append(sid)
        if len(calls) == 1:
            snapshot = subscription("active")
            retrieved_active.set()
            await resume.wait()  # A has read the old Stripe state; B must commit first.
            return snapshot
        return subscription("canceled")

    sdk = AsyncMock(side_effect=retrieve)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", sdk)
    data = subscription() if kind.startswith("customer.") else {
        "subscription": "sub_1", "customer": "cus_1", "metadata": {"organizationId": "org-alpha"}}
    first = asyncio.create_task(webhook(event(kind, "evt_A", data)))
    try:
        await asyncio.wait_for(retrieved_active.wait(), 2)
        assert (await webhook(event("customer.subscription.deleted", "evt_B", subscription("canceled")))).status_code == 200
        assert (await db.organization_billing.find_one({}))["billingStatus"] == "cancelled"
    finally:
        resume.set()
        result = await first
    assert result.status_code == 200
    doc = await db.organization_billing.find_one({})
    assert doc["billingStatus"] == "cancelled" and doc["subscriptionTier"] == "cancelled"
    assert doc["billingRevision"] == 2
    assert sdk.await_count == 3  # A's CAS miss requires a new Stripe retrieval.


async def test_bounded_cas_failure_releases_event_claim(webhook, db, monkeypatch):
    await seed(db)
    sdk = AsyncMock(return_value=subscription())
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", sdk)
    collection_type = type(db.organization_billing)
    original = collection_type.update_one

    async def contended(self, *args, **kwargs):
        if self.name == "organization_billing":
            return SimpleNamespace(matched_count=0)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(collection_type, "update_one", contended)
    response = await webhook(event())
    assert response.status_code == 503
    assert await db.stripe_events.count_documents({}) == 0
    assert sdk.await_count == 3


async def test_revisionless_billing_upgrades_on_first_write(webhook, db, monkeypatch):
    await seed(db)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", AsyncMock(return_value=subscription()))
    assert (await webhook(event())).status_code == 200
    assert (await db.organization_billing.find_one({}))["billingRevision"] == 1
    assert (await webhook(event(eid="evt_next"))).status_code == 200
    assert (await db.organization_billing.find_one({}))["billingRevision"] == 2
