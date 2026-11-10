"""Email intake attachment uploads: the intake is persisted before uploads, a
mid-batch failure purges the objects already stored for that email (recording
any it cannot purge), the Celery task retries with the original payload, and
the periodic retry reprocesses only intakes whose files are stored."""

import asyncio
import base64
import copy
from datetime import datetime, timedelta, timezone

import pytest

import services.ai_service as ai_service
import services.storage_service as storage_service
from models.billing import BillingStatus, SubscriptionTier
from services import audit_service, intake_tasks
from services.task_db import current_db

ORG = "org-intake"
PRICE = "price_intake_team"


def _b64(text):
    return base64.b64encode(text.encode()).decode()


PAYLOAD = {
    "created_at": "2026-11-09T12:00:00Z",
    "data": {
        "id": "em_recovery",
        "from": "legal@acme.test",
        "subject": "Two contracts",
        "attachments": [
            {"filename": "a.txt", "content_type": "text/plain", "content": _b64("A")},
            {"filename": "b.txt", "content_type": "text/plain", "content": _b64("B")},
        ],
    },
}


@pytest.fixture(autouse=True)
def fresh_index_cache():
    """The readiness cache is per process; start each test without it."""
    intake_tasks._intake_index_ready.clear()
    yield
    intake_tasks._intake_index_ready.clear()


@pytest.fixture
async def task_db(db, monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_TEAM_MONTHLY", PRICE)
    audit_service.init_db(db)
    await db.organization_billing.insert_one(
        {
            "id": "b-intake",
            "organizationId": ORG,
            "subscriptionTier": SubscriptionTier.TEAM,
            "billingStatus": BillingStatus.ACTIVE,
            "stripePriceId": PRICE,
        }
    )
    token = current_db.set(db)
    yield db
    current_db.reset(token)


@pytest.fixture
def storage(monkeypatch):
    """Fake S3: ``fail_on`` names the filenames whose upload raises."""
    state = {"objects": set(), "fail_on": set(), "purge_fails": set(), "uploads": 0}

    async def upload(**kwargs):
        state["uploads"] += 1
        if kwargs["filename"] in state["fail_on"]:
            raise ValueError("Failed to upload file")
        key = f"{ORG}/{kwargs['filename']}-{state['uploads']}"
        state["objects"].add(key)
        return key

    async def purge(key):
        if key in state["purge_fails"] or any(
            key.startswith(f"{ORG}/{n}-") for n in state["purge_fails"]
        ):
            return False
        state["objects"].discard(key)
        return True

    async def resolve(sender, db):
        return ORG

    async def classify(**kwargs):
        return {"contract_type": "NDA", "counterparty": "Acme", "confidence": 0.9}

    contracts = []

    async def create_contract(intake, classification, db):
        contracts.append(intake.id)
        return f"c-{len(contracts)}"

    monkeypatch.setattr(storage_service, "upload_file_to_s3", upload)
    monkeypatch.setattr(storage_service, "purge_uploaded_object", purge)
    monkeypatch.setattr(intake_tasks, "_resolve_organization_from_sender", resolve)
    monkeypatch.setattr(ai_service, "classify_intake_contract", classify)
    state["real_create_contract"] = intake_tasks._create_contract_from_intake
    monkeypatch.setattr(intake_tasks, "_create_contract_from_intake", create_contract)
    state["contracts"] = contracts
    return state


async def _only_intake(db):
    docs = await db.contract_intakes.find({}, {"_id": 0}).to_list(10)
    assert len(docs) == 1, docs
    return docs[0]


async def test_mid_batch_failure_keeps_intake_and_purges_earlier_uploads(
    task_db, storage
):
    storage["fail_on"] = {"b.txt"}
    with pytest.raises(intake_tasks.IntakeAttachmentUploadError):
        await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))

    doc = await _only_intake(task_db)
    assert doc["status"] == "failed"
    assert doc["attachments"] == [] and doc["attachments_stored"] is False
    assert doc["upload_attempts"] == 1
    assert "Attachment upload failed" in doc["error_message"]
    assert storage["objects"] == set()  # a.txt was purged, nothing leaked
    assert "orphaned_storage_keys" not in doc


async def test_task_retry_after_upload_failure_reuses_intake_and_succeeds(
    task_db, storage
):
    storage["fail_on"] = {"b.txt"}
    with pytest.raises(intake_tasks.IntakeAttachmentUploadError):
        await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))
    first = await _only_intake(task_db)

    storage["fail_on"] = set()  # storage recovers; Celery re-runs the same payload
    result = await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))
    assert result["status"] == "success", result
    assert result["intake_id"] == first["id"]

    doc = await _only_intake(task_db)
    assert doc["status"] == "routed" and doc["attachments_stored"] is True
    assert [a["filename"] for a in doc["attachments"]] == ["a.txt", "b.txt"]
    assert {a["storage_key"] for a in doc["attachments"]} == storage["objects"]
    assert storage["contracts"] == [first["id"]]


async def test_final_attempt_moves_intake_to_manual_review(task_db, storage):
    storage["fail_on"] = {"b.txt"}
    result = await intake_tasks._process_resend_webhook_async(
        copy.deepcopy(PAYLOAD), final_attempt=True
    )
    assert result["status"] == "error"
    doc = await _only_intake(task_db)
    assert doc["status"] == "manual_review"
    assert "resent" in doc["error_message"]
    assert storage["objects"] == set()


async def test_unpurgeable_objects_are_recorded_on_the_intake(task_db, storage):
    storage["fail_on"] = {"b.txt"}
    storage["purge_fails"] = {"a.txt"}
    with pytest.raises(intake_tasks.IntakeAttachmentUploadError):
        await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))
    doc = await _only_intake(task_db)
    assert doc["orphaned_storage_keys"] == sorted(storage["objects"])
    assert len(doc["orphaned_storage_keys"]) == 1


async def test_redelivery_of_processed_email_is_a_duplicate(task_db, storage):
    first = await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))
    assert first["status"] == "success"
    again = await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))
    assert again == {"status": "duplicate", "intake_id": first["intake_id"]}
    await _only_intake(task_db)
    assert storage["uploads"] == 2  # no re-upload


async def test_celery_task_retries_upload_failure_with_original_payload(
    task_db, storage, monkeypatch
):
    """The sync Celery wrapper turns an upload failure into a task retry."""
    storage["fail_on"] = {"b.txt"}
    retried = {}

    class Retry(Exception):
        pass

    def fake_retry(exc=None, **kwargs):
        retried["exc"] = exc
        return Retry()

    task = intake_tasks.process_resend_webhook
    monkeypatch.setattr(task, "retry", fake_retry)
    with pytest.raises(Retry):
        # The wrapper runs its own event loop, so call it off this loop.
        await asyncio.to_thread(task.run, copy.deepcopy(PAYLOAD))
    assert isinstance(retried["exc"], intake_tasks.IntakeAttachmentUploadError)
    doc = await _only_intake(task_db)
    assert doc["status"] == "failed" and storage["objects"] == set()


async def test_periodic_retry_reprocesses_stored_intake_only(
    task_db, storage, monkeypatch
):
    queued = []
    monkeypatch.setattr(
        intake_tasks.reprocess_intake,
        "delay",
        lambda intake_id, claim_token=None: queued.append(intake_id),
    )
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "organizationId": ORG,
        "channel": "email",
        "source_email": "legal@acme.test",
        "source_metadata": {"subject": "x"},
        "status": "failed",
        "createdAt": now,
    }
    stored_attachment = {
        "filename": "a.txt",
        "size": 1,
        "mime_type": "text/plain",
        "storage_key": f"{ORG}/a.txt-1",
        "extracted_text": "A",
    }
    await task_db.contract_intakes.insert_many(
        [
            {**base, "id": "stored", "attachments": [stored_attachment]},
            {
                **base,
                "id": "upload-failed",
                "attachments": [],
                "attachments_stored": False,
            },
            # Legacy records predate attachments_stored; files were stored.
            {**base, "id": "legacy", "attachments": [stored_attachment]},
        ]
    )
    await task_db.contract_intakes.update_one(
        {"id": "stored"}, {"$set": {"attachments_stored": True}}
    )

    result = await intake_tasks._retry_failed_intakes_async()
    assert result == {"status": "success", "retried": 2, "manual_review": 0}
    assert sorted(queued) == ["legacy", "stored"]
    skipped = await task_db.contract_intakes.find_one({"id": "upload-failed"})
    assert skipped["status"] == "failed"

    # A second, overlapping pass must not queue the claimed intakes again.
    again = await intake_tasks._retry_failed_intakes_async()
    assert again["retried"] == 0

    monkeypatch.setattr(intake_tasks, "get_db", lambda: task_db)
    out = await intake_tasks._reprocess_intake_async("stored")
    assert out["status"] == "success", out
    doc = await task_db.contract_intakes.find_one({"id": "stored"})
    assert doc["status"] == "routed" and doc["created_contract_id"] == "c-1"


async def test_late_failure_after_contract_insert_does_not_duplicate_contract(
    task_db, storage, monkeypatch
):
    """Queueing follow-up work fails after the contract is inserted; the retry
    reuses the reserved contract instead of creating a second one."""
    from models.agentic import ContractIntake, IntakeChannel
    from services import risk_tasks

    monkeypatch.setattr(
        intake_tasks,
        "_create_contract_from_intake",
        storage["real_create_contract"],
    )
    queued = []
    broker = {"down": True}

    def assess(contract_id, **kwargs):
        if broker["down"]:
            raise RuntimeError("broker unavailable")
        queued.append(contract_id)

    monkeypatch.setattr(risk_tasks.assess_contract_risk, "delay", assess)
    intake = ContractIntake(
        organizationId=ORG,
        channel=IntakeChannel.EMAIL,
        source_email="legal@acme.test",
        attachments=[
            {"filename": "a.txt", "mime_type": "text/plain", "storage_key": "k"}
        ],
    )
    await task_db.contract_intakes.insert_one(
        {**intake.model_dump(), "attachments_stored": True}
    )

    failed = await intake_tasks._process_stored_intake(intake, task_db)
    assert failed["status"] == "error"
    doc = await task_db.contract_intakes.find_one({"id": intake.id})
    assert doc["status"] == "failed" and doc["created_contract_id"]
    assert await task_db.contracts.count_documents({}) == 1

    broker["down"] = False
    fresh = ContractIntake(
        **(await task_db.contract_intakes.find_one({"id": intake.id}, {"_id": 0}))
    )
    ok = await intake_tasks._process_stored_intake(fresh, task_db)
    assert ok["status"] == "success", ok
    assert ok["contract_id"] == doc["created_contract_id"]
    assert await task_db.contracts.count_documents({}) == 1
    assert queued == [doc["created_contract_id"]]


async def test_concurrent_retry_cannot_claim_an_already_claimed_intake(
    task_db, storage
):
    """Two retries of the same failed intake: only the first claim proceeds."""
    storage["fail_on"] = {"b.txt"}
    with pytest.raises(intake_tasks.IntakeAttachmentUploadError):
        await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))
    doc = await _only_intake(task_db)
    reset = {"id": doc["id"], "status": "received", "attachments": []}

    assert await intake_tasks._save_intake(task_db, dict(reset), replace=True)
    # The record is no longer "failed", so a second concurrent claim loses
    # and must not upload or overwrite the winner's attachments.
    assert not await intake_tasks._save_intake(task_db, dict(reset), replace=True)


def _iso(delta_minutes=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)).isoformat()


async def test_stale_claims_are_recovered_or_handed_to_manual_review(
    task_db, storage, monkeypatch
):
    """A worker died mid-flight: stored intakes are reprocessed, unstored ones
    are rolled back and left for manual review, live claims are untouched."""
    queued = []
    monkeypatch.setattr(
        intake_tasks.reprocess_intake,
        "delay",
        lambda intake_id, claim_token=None: queued.append(intake_id),
    )
    storage["objects"].add(f"{ORG}/a.txt-1")
    stale = _iso(-60)
    base = {
        "organizationId": ORG,
        "channel": "email",
        "source_email": "legal@acme.test",
        "source_metadata": {"subject": "x"},
        "createdAt": _iso(-90),
    }
    attachment = {"filename": "a.txt", "storage_key": f"{ORG}/b.txt-9"}
    await task_db.contract_intakes.insert_many(
        [
            {
                **base,
                "id": "stale-stored",
                "status": "processing",
                "claimed_at": stale,
                "attachments_stored": True,
                "attachments": [attachment],
            },
            {
                **base,
                "id": "stale-uploading",
                "status": "received",
                "claimed_at": stale,
                "attachments_stored": False,
                "attachments": [],
                "pending_storage_keys": [f"{ORG}/a.txt-1"],
            },
            {
                **base,
                "id": "live",
                "status": "processing",
                "claimed_at": _iso(-1),
                "attachments_stored": True,
                "attachments": [attachment],
            },
            {
                **base,
                "id": "exhausted",
                "status": "failed",
                "claimed_at": stale,
                "attachments_stored": True,
                "attachments": [attachment],
                "processing_attempts": intake_tasks.MAX_INTAKE_PROCESSING_ATTEMPTS,
            },
        ]
    )

    result = await intake_tasks._retry_failed_intakes_async()
    assert result == {"status": "success", "retried": 1, "manual_review": 2}
    assert queued == ["stale-stored"]

    docs = {
        d["id"]: d
        for d in await task_db.contract_intakes.find({}, {"_id": 0}).to_list(10)
    }
    assert docs["stale-stored"]["status"] == "received"
    assert docs["stale-stored"]["processing_attempts"] == 1
    assert docs["stale-stored"]["claimed_at"] > stale
    assert docs["live"]["status"] == "processing"
    assert docs["stale-uploading"]["status"] == "manual_review"
    assert "resent" in docs["stale-uploading"]["error_message"]
    assert storage["objects"] == set()  # the interrupted upload was purged
    assert docs["exhausted"]["status"] == "manual_review"

    # The fresh claim is live again, so an overlapping pass does nothing.
    again = await intake_tasks._retry_failed_intakes_async()
    assert again == {"status": "success", "retried": 0, "manual_review": 0}


async def test_uploads_are_tracked_on_the_intake_while_in_flight(task_db, storage):
    """Every stored object is recorded before the batch completes."""
    seen = []
    real_upload = storage_service.upload_file_to_s3

    async def upload(**kwargs):
        doc = await task_db.contract_intakes.find_one({})
        seen.append(list(doc["pending_storage_keys"]))
        return await real_upload(**kwargs)

    storage_service.upload_file_to_s3 = upload
    try:
        result = await intake_tasks._process_resend_webhook_async(
            copy.deepcopy(PAYLOAD)
        )
    finally:
        storage_service.upload_file_to_s3 = real_upload
    assert result["status"] == "success"
    assert seen[0] == [] and len(seen[1]) == 1  # a.txt tracked before b.txt
    doc = await _only_intake(task_db)
    assert doc["pending_storage_keys"] == [] and doc["attachments_stored"] is True


async def test_transient_error_before_persistence_is_retried(
    task_db, storage, monkeypatch
):
    async def flaky(sender, db):
        raise ConnectionError("mongo blip")

    monkeypatch.setattr(intake_tasks, "_resolve_organization_from_sender", flaky)
    with pytest.raises(intake_tasks.IntakeRetryableError):
        await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))
    assert await task_db.contract_intakes.count_documents({}) == 0
    # The final attempt reports the error instead of retrying forever.
    final = await intake_tasks._process_resend_webhook_async(
        copy.deepcopy(PAYLOAD), final_attempt=True
    )
    assert final["status"] == "error"


async def test_entitlement_lookup_failure_is_retried(task_db, storage, monkeypatch):
    import routes.billing as billing

    async def down(*args, **kwargs):
        raise ConnectionError("mongo blip")

    monkeypatch.setattr(billing, "background_ai_entitlement", down)
    with pytest.raises(intake_tasks.IntakeRetryableError):
        await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))
    assert storage["uploads"] == 0


async def test_task_does_not_process_without_the_unique_index(
    task_db, storage, monkeypatch
):
    async def index_down(database):
        raise RuntimeError("createIndexes failed")

    monkeypatch.setattr(intake_tasks, "ensure_intake_idempotency_index", index_down)
    with pytest.raises(intake_tasks.IntakeRetryableError):
        await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))
    assert await task_db.contract_intakes.count_documents({}) == 0
    assert storage["uploads"] == 0


async def test_concurrent_first_deliveries_create_one_intake(task_db, storage):
    """The unique index turns a racing second insert into a duplicate."""
    await intake_tasks.ensure_intake_idempotency_index(task_db)
    doc = {
        "id": "i-1",
        "organizationId": ORG,
        "idempotency_key": "k",
        "status": "received",
    }
    assert await intake_tasks._save_intake(task_db, dict(doc), replace=False)
    assert not await intake_tasks._save_intake(
        task_db, {**doc, "id": "i-2"}, replace=False
    )
    assert await task_db.contract_intakes.count_documents({}) == 1


async def test_failed_tracking_write_still_rolls_back_the_new_object(
    task_db, storage, monkeypatch
):
    """The object is purged even if recording it on the intake fails."""
    coll_type = type(task_db.contract_intakes)
    real_update_one = coll_type.update_one

    async def flaky_update_one(self, filter, update, *args, **kwargs):
        if "pending_storage_keys" in (update.get("$push") or {}):
            raise ConnectionError("mongo blip")
        return await real_update_one(self, filter, update, *args, **kwargs)

    monkeypatch.setattr(coll_type, "update_one", flaky_update_one)
    with pytest.raises(intake_tasks.IntakeRetryableError):
        await intake_tasks._process_resend_webhook_async(copy.deepcopy(PAYLOAD))
    assert storage["uploads"] == 1
    assert storage["objects"] == set()  # the untracked upload was purged
    doc = await _only_intake(task_db)
    assert doc["status"] == "failed" and "orphaned_storage_keys" not in doc


async def test_worker_that_lost_its_claim_does_no_work(task_db, storage):
    """A stale-claim recovery hands the intake to worker B; worker A, still
    holding the old token, stops before classification or contract creation."""
    from models.agentic import ContractIntake, IntakeChannel

    intake = ContractIntake(
        organizationId=ORG,
        channel=IntakeChannel.EMAIL,
        source_email="legal@acme.test",
        attachments=[{"filename": "a.txt", "storage_key": "k"}],
    )
    await task_db.contract_intakes.insert_one(
        {
            **intake.model_dump(),
            "status": "received",
            "attachments_stored": True,
            "claim_token": "token-b",
        }
    )
    lost = await intake_tasks._process_stored_intake(intake, task_db, "token-a")
    assert lost["status"] == "skipped" and lost["reason"] == "claim_lost"
    assert storage["contracts"] == []
    won = await intake_tasks._process_stored_intake(intake, task_db, "token-b")
    assert won["status"] == "success" and storage["contracts"] == [intake.id]


async def test_concurrent_contract_creation_yields_one_contract(
    task_db, storage, monkeypatch
):
    """Two workers racing past the claim still create exactly one contract."""
    from models.agentic import ContractIntake, IntakeChannel
    from services import risk_tasks

    queued = []
    monkeypatch.setattr(
        risk_tasks.assess_contract_risk, "delay", lambda cid, **k: queued.append(cid)
    )
    create = storage["real_create_contract"]
    intake = ContractIntake(
        organizationId=ORG,
        channel=IntakeChannel.EMAIL,
        source_email="legal@acme.test",
        attachments=[{"filename": "a.txt", "storage_key": "k"}],
    )
    await task_db.contract_intakes.insert_one(intake.model_dump())
    ids = await asyncio.gather(
        create(intake.model_copy(), {"contract_type": "NDA"}, task_db),
        create(intake.model_copy(), {"contract_type": "NDA"}, task_db),
    )
    assert ids[0] == ids[1]
    assert await task_db.contracts.count_documents({}) == 1
    doc = await task_db.contract_intakes.find_one({"id": intake.id})
    assert doc["created_contract_id"] == ids[0]
    assert len(queued) >= 1 and set(queued) == {ids[0]}
