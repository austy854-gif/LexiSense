"""
Celery tasks for Zero-Training Business Intake.
Handles Resend webhook processing, email attachment parsing, and contract classification.
"""

import asyncio
import logging
import base64
import hmac
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from celery import shared_task
import httpx
import pypdf as PyPDF2
import io

# Imported at module scope because ContractIntake is referenced in a function
# annotation, which Python evaluates when the `async def` statement runs.
from models.agentic import ContractIntake

logger = logging.getLogger(__name__)

# Database reference
_db = None


def set_database(database):
    """Set the database reference for tasks."""
    global _db
    _db = database


def get_db():
    """Database for the current task (ContextVar set per task by
    services.celery_app), falling back to an explicitly set module database."""
    from services.task_db import current_db

    db = current_db.get()
    return db if db is not None else _db


def verify_resend_signature(payload: bytes, signature: str, secret: str) -> bool:
    """DEPRECATED: plain hex HMAC, not Resend's (Svix) scheme. Unused; kept only
    for import compatibility. Use :func:`utils.svix.verify` instead."""
    if not secret or not signature:
        return False

    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    return hmac.compare_digest(expected, signature)


def extract_text_from_pdf(pdf_content: bytes) -> str:
    """Extract text from PDF content."""
    try:
        pdf_file = io.BytesIO(pdf_content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text_parts = []
        for page in pdf_reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n".join(text_parts)
    except Exception as e:
        logger.error(f"PDF text extraction failed: {e}")
        return ""


def extract_text_from_docx(docx_content: bytes) -> str:
    """Extract text from DOCX content."""
    try:
        # python-docx would be needed for full support
        # For now, return placeholder
        return "[DOCX content - text extraction requires python-docx]"
    except Exception as e:
        logger.error(f"DOCX text extraction failed: {e}")
        return ""


#: A claimed intake (received/processing/classified) whose claim is older than
#: this has no live worker; the periodic retry recovers it.
INTAKE_CLAIM_LEASE = timedelta(minutes=30)
#: Processing attempts (claims) before an intake is left for manual review.
MAX_INTAKE_PROCESSING_ATTEMPTS = 5
INTAKE_IDEMPOTENCY_INDEX = "uq_contract_intakes_idempotency"
_intake_index_ready = set()


async def ensure_intake_idempotency_index(db) -> None:
    """Create (or confirm) the unique per-email intake index.

    Raises if the index cannot be confirmed; callers must not accept or process
    intakes until it succeeds. Success is cached per database handle, so a
    failure is retried on the next webhook or task.
    """
    if id(db) in _intake_index_ready:
        return
    await db.contract_intakes.create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name=INTAKE_IDEMPOTENCY_INDEX,
    )
    _intake_index_ready.add(id(db))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_claim() -> Dict[str, str]:
    """Fields every claim writes: a lease timestamp and an ownership token."""
    import uuid

    return {"claimed_at": _now_iso(), "claim_token": uuid.uuid4().hex}


class IntakeRetryableError(RuntimeError):
    """A transient intake failure; the Celery task retries the original payload."""


class IntakeAttachmentUploadError(IntakeRetryableError):
    """Storing an intake's attachments failed; the Celery task should retry.

    Only the original webhook payload carries the attachment bytes, so a failed
    upload is retried by re-running the same task (same payload), never by the
    periodic retry, which only reprocesses intakes whose files are stored.
    """


def _intake_idempotency_key(organization_id: str, payload: Dict[str, Any]) -> str:
    """Stable key for one inbound email so task retries and webhook redeliveries
    reuse a single intake record."""
    email_id = (payload.get("data") or {}).get("id")
    if email_id:
        basis = f"resend:{email_id}"
    else:
        basis = (
            "payload:"
            + hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest()
        )
    return hashlib.sha256(f"{organization_id}:{basis}".encode()).hexdigest()


async def _rollback_uploads(storage_keys: List[str]) -> List[str]:
    """Purge objects stored for an intake whose upload batch failed.

    Returns the keys that could not be purged so they stay recorded on the
    intake (``orphaned_storage_keys``) instead of leaking silently.
    """
    from services.storage_service import purge_uploaded_object

    retained = []
    for key in storage_keys:
        try:
            purged = await purge_uploaded_object(key)
        except Exception as exc:  # rollback must not mask the upload failure
            logger.error("Intake upload rollback raised (%s)", type(exc).__name__)
            purged = False
        if not purged:
            retained.append(key)
    return retained


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def process_resend_webhook(self, payload: Dict[str, Any]):
    """Process incoming Resend webhook for contract intake."""
    if get_db() is None:
        logger.error("Database not initialized for intake processing")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Processing Resend webhook for contract intake")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _process_resend_webhook_async(
                    payload, final_attempt=self.request.retries >= self.max_retries
                )
            )
        finally:
            loop.close()
        return result
    except Exception as e:
        logger.error(f"Resend webhook processing failed: {e}")
        raise self.retry(exc=e)


async def _process_resend_webhook_async(
    payload: Dict[str, Any], final_attempt: bool = False
):
    """Async implementation of Resend webhook processing.

    The intake record is persisted before any attachment is uploaded. If an
    upload fails, objects already stored for this email are purged (any that
    cannot be purged are recorded on the intake), the intake is marked
    ``failed`` and :class:`IntakeAttachmentUploadError` is raised so the Celery
    task retries with the original payload. On the final attempt the intake is
    moved to ``manual_review`` instead of raising.
    """
    from models.agentic import ContractIntake, IntakeChannel, IntakeStatus
    from services.storage_service import upload_file_to_s3

    db = get_db()
    if db is None:
        return {"status": "error", "message": "Database not available"}

    intake = None
    stored = None  # id of an intake whose attachments are stored and referenced
    claim_token: Optional[str] = None  # token of the claim this run holds on it
    processed_attachments: List[Dict[str, Any]] = []
    # Every object stored for this email, recorded before any write that can
    # fail, so a rollback always covers it.
    uploaded_keys: List[str] = []
    try:
        # Duplicate protection depends on the unique index; never process
        # without it (a failure here is retried by the task).
        await ensure_intake_idempotency_index(db)

        # The Svix signature is verified on the raw request body by the
        # /agentic/intake/webhook route before this task is queued; the
        # re-serialised payload here could never match the original bytes.

        # Extract email data from Resend webhook
        # Resend webhook format: https://resend.com/docs/webhooks
        email_data = payload.get("data", {})

        # Get sender
        from_email = email_data.get("from", "")
        if isinstance(from_email, dict):
            from_email = from_email.get("email", "")

        # Get subject
        subject = email_data.get("subject", "")

        # Get attachments
        attachments = email_data.get("attachments", [])

        # Get organization - lookup by domain mapping
        organization_id = await _resolve_organization_from_sender(from_email, db)
        if not organization_id:
            return {"status": "error", "message": "Could not resolve organization"}

        idempotency_key = _intake_idempotency_key(organization_id, payload)
        existing = await db.contract_intakes.find_one(
            {"organizationId": organization_id, "idempotency_key": idempotency_key},
            {"_id": 0},
        )
        if existing and existing.get("status") != IntakeStatus.FAILED.value:
            # Webhook redelivery or a task retry after the intake moved on.
            return {"status": "duplicate", "intake_id": existing["id"]}
        if existing and existing.get("attachments_stored"):
            # Files are already stored; only the processing step failed.
            # Claim it so a concurrent retry pass cannot process it twice.
            claim = _new_claim()
            claimed = await db.contract_intakes.update_one(
                {"id": existing["id"], "status": IntakeStatus.FAILED.value},
                {
                    "$set": {"status": IntakeStatus.RECEIVED.value, **claim},
                    "$inc": {"processing_attempts": 1},
                },
            )
            if not claimed.modified_count:
                return {"status": "duplicate", "intake_id": existing["id"]}
            stored = existing["id"]
            claim_token = claim["claim_token"]
            return await _process_stored_intake(
                ContractIntake(**existing), db, claim_token
            )

        # Create intake record
        intake = ContractIntake(
            organizationId=organization_id,
            channel=IntakeChannel.EMAIL,
            source_email=from_email,
            source_metadata={
                "subject": subject,
                "resend_id": email_data.get("id"),
                "received_at": payload.get("created_at"),
                "headers": payload.get("headers", {}),
            },
            status=IntakeStatus.RECEIVED,
        )
        if existing:
            # A previous attempt failed while uploading: reuse its record.
            intake.id = existing["id"]
            intake.createdAt = existing.get("createdAt", intake.createdAt)
            intake.received_at = existing.get("received_at", intake.received_at)

        # Billing gate BEFORE storing attachments or calling the AI classifier:
        # an emailed contract is an upload plus AI work, so orgs without AI
        # entitlement (trial ended, cancelled, read-only past due, unverified
        # plan) get a record of the email for manual review and nothing else.
        from routes.billing import SKIPPED_NO_ENTITLEMENT, background_ai_entitlement

        if (
            await background_ai_entitlement(
                organization_id, db, f"email intake from {from_email}"
            )
            is None
        ):
            intake.status = IntakeStatus.MANUAL_REVIEW
            intake_doc = intake.model_dump()
            intake_doc.update(
                {
                    "idempotency_key": idempotency_key,
                    "attachments_stored": False,
                    "claimed_at": _now_iso(),
                    "error_message": "Not processed: the organization's plan does not include uploads or AI analysis right now.",
                    "processing_skip_reason": "no_ai_entitlement",
                    "playbook_analysis_status": SKIPPED_NO_ENTITLEMENT,
                    "processing_completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            if not await _save_intake(db, intake_doc, replace=bool(existing)):
                return {"status": "duplicate", "intake_id": intake.id}
            return {
                "status": SKIPPED_NO_ENTITLEMENT,
                "intake_id": intake.id,
                "reason": "no_ai_entitlement",
            }

        # Persist the intake before uploading so an upload failure can never
        # drop the email: the record exists (and is retried) either way.
        intake_doc = intake.model_dump()
        intake_doc.update(
            {
                "idempotency_key": idempotency_key,
                "attachments_stored": False,
                "pending_storage_keys": [],
                **_new_claim(),
            }
        )
        if not await _save_intake(db, intake_doc, replace=bool(existing)):
            return {"status": "duplicate", "intake_id": intake.id}

        # Process attachments
        processed_attachments = []
        for attachment in attachments:
            # Resend attachment format: content (base64), filename, content_type
            content_b64 = attachment.get("content", "")
            filename = attachment.get("filename", "unknown")
            content_type = attachment.get("content_type", "application/octet-stream")

            # Decode base64
            try:
                file_content = base64.b64decode(content_b64)
            except Exception as e:
                logger.error(f"Failed to decode attachment {filename}: {e}")
                continue

            # Extract text for classification
            extracted_text = ""
            if content_type == "application/pdf":
                extracted_text = extract_text_from_pdf(file_content)
            elif content_type == "text/plain":
                try:
                    extracted_text = file_content.decode("utf-8", errors="ignore")
                except Exception:
                    extracted_text = ""
            elif content_type in [
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ]:
                extracted_text = extract_text_from_docx(file_content)

            # Upload to S3. Any failure rolls back this email's stored objects.
            try:
                storage_key = await upload_file_to_s3(
                    file_content=file_content,
                    filename=filename,
                    organization_id=organization_id,
                    content_type=content_type,
                )
            except Exception as exc:
                return await _fail_attachment_upload(
                    db,
                    intake,
                    list(uploaded_keys),
                    exc,
                    final_attempt,
                )
            uploaded_keys.append(storage_key)

            # Track the object on the intake straight away so a worker crash
            # mid-batch leaves it recoverable rather than orphaned.
            await db.contract_intakes.update_one(
                {"id": intake.id}, {"$push": {"pending_storage_keys": storage_key}}
            )
            processed_attachments.append(
                {
                    "filename": filename,
                    "size": len(file_content),
                    "mime_type": content_type,
                    "storage_key": storage_key,
                    "extracted_text": (
                        extracted_text[:5000] if extracted_text else ""
                    ),  # Store first 5000 chars
                }
            )

        intake.attachments = processed_attachments

        # If no attachments, mark as failed (nothing stored, nothing to retry)
        if not processed_attachments:
            await db.contract_intakes.update_one(
                {"id": intake.id},
                {
                    "$set": {
                        "status": IntakeStatus.FAILED,
                        "error_message": "No valid attachments found",
                        "processing_completed_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }
                },
            )
            return {
                "status": "error",
                "message": "No valid attachments",
                "intake_id": intake.id,
            }

        # Attachments are stored and referenced by the persisted intake, so a
        # later processing failure is recoverable by the periodic retry.
        await db.contract_intakes.update_one(
            {"id": intake.id},
            {
                "$set": {
                    "attachments": processed_attachments,
                    "attachments_stored": True,
                    "pending_storage_keys": [],
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        stored = intake.id
        claim_token = intake_doc["claim_token"]
        return await _process_stored_intake(intake, db, claim_token)

    except IntakeRetryableError:
        raise
    except Exception as e:
        logger.error(f"Intake processing error: {e}")
        if stored:
            # Files are referenced by the intake; the periodic retry (or the
            # stale-claim recovery, if this write fails too) reprocesses them.
            await _mark_intake_failed(db, stored, str(e), claim_token)
            return {"status": "error", "message": str(e)}
        if intake is None:
            # Nothing persisted yet (e.g. a transient database error).
            if final_attempt:
                return {"status": "error", "message": str(e)}
            raise IntakeRetryableError(
                f"Intake processing failed before persistence ({type(e).__name__})"
            ) from e
        # Persisted but files not yet referenced: purge this batch and retry.
        return await _fail_attachment_upload(
            db,
            intake,
            list(uploaded_keys),
            e,
            final_attempt,
            stage="Intake processing",
        )


async def _save_intake(db, intake_doc: dict, replace: bool) -> bool:
    """Insert a new intake, or reset the failed record being retried.

    Returns False if a concurrent delivery already created or claimed the
    record; the caller must then stop without uploading anything.
    """
    from pymongo.errors import DuplicateKeyError

    if replace:
        doc = {k: v for k, v in intake_doc.items() if k not in ("id", "createdAt")}
        doc["updatedAt"] = datetime.now(timezone.utc).isoformat()
        # Conditional on "failed": exactly one concurrent retry can claim it.
        result = await db.contract_intakes.update_one(
            {"id": intake_doc["id"], "status": "failed"}, {"$set": doc}
        )
        return result.modified_count == 1
    try:
        await db.contract_intakes.insert_one(dict(intake_doc))
    except DuplicateKeyError:
        return False
    return True


def _owned(intake_id: str, claim_token: Optional[str]) -> Dict[str, Any]:
    """Filter matching the intake only while ``claim_token`` still owns it."""
    flt: Dict[str, Any] = {"id": intake_id}
    if claim_token is not None:
        flt["claim_token"] = claim_token
    return flt


async def _owned_update(db, intake_id, claim_token, fields: Dict[str, Any]) -> bool:
    """Apply ``fields`` while the claim is held; False once it has been lost."""
    result = await db.contract_intakes.update_one(
        _owned(intake_id, claim_token), {"$set": fields}
    )
    return claim_token is None or result.matched_count > 0


async def _mark_intake_failed(
    db, intake_id: str, message: str, claim_token: Optional[str] = None
) -> None:
    """Record a processing failure on a persisted intake (only while the
    caller still owns the claim, when ``claim_token`` is given)."""
    from models.agentic import IntakeStatus

    if not intake_id:
        return
    await db.contract_intakes.update_one(
        _owned(intake_id, claim_token),
        {
            "$set": {
                "status": IntakeStatus.FAILED,
                "error_message": message,
                "processing_completed_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )


async def _fail_attachment_upload(
    db,
    intake,
    stored_keys: List[str],
    exc: Exception,
    final_attempt: bool,
    stage: str = "Attachment upload",
):
    """Roll back a failed upload batch and hand the intake to the task retry."""
    from models.agentic import IntakeStatus

    retained = await _rollback_uploads(stored_keys)
    error_type = type(exc).__name__
    now = datetime.now(timezone.utc).isoformat()
    update = {
        "$set": {
            "status": (
                IntakeStatus.MANUAL_REVIEW if final_attempt else IntakeStatus.FAILED
            ),
            "attachments": [],
            "attachments_stored": False,
            "pending_storage_keys": [],
            "error_message": (
                f"{stage} failed ({error_type}); "
                + (
                    "retries exhausted, the email must be resent."
                    if final_attempt
                    else "retrying."
                )
            ),
            "processing_completed_at": now,
            "updatedAt": now,
        },
        "$inc": {"upload_attempts": 1},
    }
    if retained:
        update["$addToSet"] = {"orphaned_storage_keys": {"$each": retained}}
        logger.error(
            "Intake upload rollback left %d object(s); recorded on intake %s",
            len(retained),
            intake.id,
        )
    await db.contract_intakes.update_one({"id": intake.id}, update)
    if final_attempt:
        logger.error(
            "Intake %s attachment upload failed on the final attempt (%s)",
            intake.id,
            error_type,
        )
        return {
            "status": "error",
            "message": f"{stage} failed",
            "intake_id": intake.id,
        }
    raise IntakeAttachmentUploadError(
        f"{stage} failed for intake {intake.id} ({error_type})"
    ) from exc


async def _process_stored_intake(intake, db, claim_token: Optional[str] = None):
    """Classify, route and create the contract for an intake whose
    attachments are already stored. Safe to rerun after a failure.

    ``claim_token`` is the token written by the claim that handed this intake
    to the caller; if another claim has replaced it, the caller no longer owns
    the intake and stops without doing any work.
    """
    from models.agentic import IntakeStatus
    from services.ai_service import classify_intake_contract

    try:
        organization_id = intake.organizationId
        from_email = intake.source_email
        subject = intake.source_metadata.get("subject", "")
        processed_attachments = intake.attachments

        # Update status to processing, only while this caller owns the claim.
        owner = {"id": intake.id}
        if claim_token is not None:
            owner.update(
                {"status": IntakeStatus.RECEIVED.value, "claim_token": claim_token}
            )
        moved = await db.contract_intakes.update_one(
            owner,
            {
                "$set": {
                    "status": IntakeStatus.PROCESSING,
                    "processing_started_at": _now_iso(),
                    "claimed_at": _now_iso(),
                }
            },
        )
        if claim_token is not None and not moved.modified_count:
            return {"status": "skipped", "intake_id": intake.id, "reason": "claim_lost"}

        # Classify the contract using AI
        # Use first attachment text for classification
        first_attachment = processed_attachments[0]

        # Use extracted text for classification
        extracted_text = first_attachment.get("extracted_text", "")

        classification = await classify_intake_contract(
            sender_email=from_email,
            subject=subject,
            filename=first_attachment.get("filename", "unknown"),
            mime_type=first_attachment.get("mime_type", "application/octet-stream"),
            extracted_text=extracted_text,
        )

        lost = {"status": "skipped", "intake_id": intake.id, "reason": "claim_lost"}

        # Update with classification (and renew the lease), only while owned.
        if not await _owned_update(
            db,
            intake.id,
            claim_token,
            {
                "status": IntakeStatus.CLASSIFIED,
                "claimed_at": _now_iso(),
                "classified_type": classification.get("contract_type"),
                "classified_counterparty": classification.get("counterparty"),
                "confidence": classification.get("confidence", 0.0),
                "classification_reasoning": classification.get("reasoning"),
                "risk_indicators": classification.get("risk_indicators", []),
            },
        ):
            return lost

        # Auto-route to playbook if applicable
        playbook_id = await _find_applicable_playbook(
            organization_id,
            classification.get("contract_type"),
            classification.get("counterparty"),
            db,
        )

        if playbook_id:
            # Keep the in-memory record in sync: _create_contract_from_intake
            # reads intake.playbook_id to decide whether to run the playbook.
            intake.playbook_id = playbook_id
            if not await _owned_update(
                db, intake.id, claim_token, {"playbook_id": playbook_id}
            ):
                return lost

        # Auto-assign to team/user based on rules
        assigned_to = await _auto_assign_intake(organization_id, classification, db)
        fields = {"claimed_at": _now_iso()}
        if assigned_to:
            fields["assigned_to"] = assigned_to
        # Ownership check right before the contract (and its follow-up tasks).
        if not await _owned_update(db, intake.id, claim_token, fields):
            return lost

        # Create contract record from intake (optional - could wait for human review)
        # Idempotent: a rerun after a late failure reuses the reserved contract.
        contract_id = await _create_contract_from_intake(intake, classification, db)

        # Mark as routed only once the contract exists, so "routed" is final.
        routed = {
            "status": IntakeStatus.ROUTED,
            "processing_completed_at": _now_iso(),
        }
        if contract_id:
            routed["created_contract_id"] = contract_id
        if not await _owned_update(db, intake.id, claim_token, routed):
            return lost

        logger.info(f"Intake processed successfully: {intake.id}")
        return {
            "status": "success",
            "intake_id": intake.id,
            "contract_id": contract_id,
            "classified_type": classification.get("contract_type"),
            "confidence": classification.get("confidence"),
        }
    except Exception as e:
        logger.error(f"Intake processing error: {e}")
        await _mark_intake_failed(db, intake.id, str(e), claim_token)
        return {"status": "error", "message": str(e), "intake_id": intake.id}


async def _resolve_organization_from_sender(sender_email: str, db) -> Optional[str]:
    """Resolve organization ID from sender email domain using domain mapping."""
    if not sender_email:
        return None

    # Extract domain
    domain = sender_email.split("@")[-1].lower() if "@" in sender_email else ""
    if not domain:
        return None

    # Look up organization by email domain in domain mapping collection
    domain_mapping = await db.organization_domains.find_one(
        {"domain": domain, "verified": True}, {"_id": 0, "organizationId": 1}
    )

    if domain_mapping:
        return domain_mapping["organizationId"]

    # Fallback: look up organization by domains array
    org = await db.organizations.find_one({"domains": domain}, {"_id": 0, "id": 1})

    if org:
        return org["id"]

    # Fallback: find org with matching email in users
    user = await db.users.find_one(
        {"email": sender_email}, {"_id": 0, "organizationId": 1}
    )

    if user:
        return user["organizationId"]

    return None


async def _find_applicable_playbook(
    org_id: str, contract_type: str, counterparty: str, db
) -> Optional[str]:
    """Find an applicable playbook for the contract."""
    if not contract_type:
        return None

    playbook = await db.legal_playbooks.find_one(
        {
            "organizationId": org_id,
            "is_active": True,
            "$or": [
                {"contract_types": contract_type},
                {"contract_types": []},
            ],
        },
        {"_id": 0, "id": 1},
    )

    if playbook:
        return playbook["id"]

    # Try default playbook
    default_playbook = await db.legal_playbooks.find_one(
        {"organizationId": org_id, "is_default": True, "is_active": True},
        {"_id": 0, "id": 1},
    )

    return default_playbook["id"] if default_playbook else None


async def _auto_assign_intake(org_id: str, classification: Dict, db) -> Optional[str]:
    """Auto-assign intake to a user based on workload and expertise."""
    # Get admin/manager users
    users = await db.users.find(
        {
            "organizationId": org_id,
            "role": {"$in": ["admin", "manager"]},
            "isActive": True,
        },
        {"_id": 0, "id": 1, "email": 1},
    ).to_list(20)

    if not users:
        return None

    # Simple round-robin or least-loaded assignment
    # In production, would check current workload
    return users[0]["id"]


async def _create_contract_from_intake(
    intake: ContractIntake, classification: Dict, db
) -> Optional[str]:
    """Create a contract record from processed intake."""
    from models.contract import Contract
    from pymongo.errors import DuplicateKeyError

    # Use first attachment for contract creation
    if not intake.attachments:
        return None

    attachment = intake.attachments[0]

    # A previous attempt may have reserved (and possibly inserted) a contract
    # before failing later on; reuse it instead of creating a second one.
    reserved_id = intake.created_contract_id
    if not reserved_id:
        current = await db.contract_intakes.find_one(
            {"id": intake.id}, {"_id": 0, "created_contract_id": 1}
        )
        reserved_id = (current or {}).get("created_contract_id")
    if reserved_id and await db.contracts.find_one(
        {"id": reserved_id, "organizationId": intake.organizationId}, {"_id": 1}
    ):
        contract_id = reserved_id
    else:
        # Initialize workflow history with initial draft state
        initial_workflow_history = [
            {
                "fromStatus": None,
                "toStatus": "draft",
                "action": "created_from_intake",
                "userId": intake.assigned_to
                or intake.source_metadata.get("resend_id", "system"),
                "userEmail": intake.source_email,
                "comment": "Contract created from email intake",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]

        contract = Contract(
            **({"id": reserved_id} if reserved_id else {}),
            organizationId=intake.organizationId,
            uploadedBy=intake.assigned_to
            or intake.source_metadata.get("resend_id", "system"),
            title=f"Intake: {attachment.get('filename', 'Contract')}",
            counterparty=classification.get("counterparty"),
            contractType=classification.get("contract_type", "General"),
            status="draft",
            fileName=attachment.get("filename"),
            fileSize=attachment.get("size"),
            mimeType=attachment.get("mime_type"),
            storageKey=attachment.get("storage_key"),
            tags=["intake", "email"],
            workflowHistory=initial_workflow_history,
            createdAt=datetime.now(timezone.utc).isoformat(),
            updatedAt=datetime.now(timezone.utc).isoformat(),
        )

        if not reserved_id:
            # Reserve the id atomically before inserting: only the winner
            # inserts, and any failure after this point is retried against
            # the same contract.
            won = await db.contract_intakes.update_one(
                {"id": intake.id, "created_contract_id": None},
                {"$set": {"created_contract_id": contract.id}},
            )
            if not won.modified_count:
                # Another worker reserved first; it inserts and queues work.
                current = await db.contract_intakes.find_one(
                    {"id": intake.id}, {"_id": 0, "created_contract_id": 1}
                )
                return (current or {}).get("created_contract_id")
        try:
            await db.contracts.insert_one(contract.model_dump())
        except DuplicateKeyError:
            pass  # the reserved contract was inserted concurrently
        contract_id = contract.id

    # Trigger risk assessment
    from services.risk_tasks import assess_contract_risk

    assess_contract_risk.delay(contract_id, force_refresh=True)

    # Trigger obligation extraction if active
    if classification.get("contract_type") in [
        "Service Agreement",
        "Lease",
        "Partnership",
        "Licensing",
        "Purchase Agreement",
    ]:
        from services.obligation_tasks import extract_obligations_from_contract

        extract_obligations_from_contract.delay(contract_id, force_refresh=True)

    # Trigger playbook analysis if playbook assigned -- only for orgs entitled
    # to AI; otherwise record that it was skipped (no crash, no AI call).
    if intake.playbook_id:
        from routes.billing import SKIPPED_NO_ENTITLEMENT, background_ai_entitlement

        entitled = await background_ai_entitlement(
            intake.organizationId, db, f"intake playbook analysis of {contract_id}"
        )
        if entitled is None:
            await db.contract_intakes.update_one(
                {"id": intake.id, "organizationId": intake.organizationId},
                {
                    "$set": {
                        "playbook_analysis_status": SKIPPED_NO_ENTITLEMENT,
                        "playbook_analysis_skip_reason": "no_ai_entitlement",
                        "updatedAt": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
        else:
            from services.playbook_tasks import analyze_contract_with_playbook

            analyze_contract_with_playbook.delay(
                contract_id, intake.playbook_id, auto_apply=False
            )
            await db.contract_intakes.update_one(
                {"id": intake.id, "organizationId": intake.organizationId},
                {"$set": {"playbook_analysis_status": "queued"}},
            )

    return contract_id


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def retry_failed_intakes(self):
    """Retry failed intake processing."""
    if get_db() is None:
        logger.error("Database not initialized for intake retry")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Retrying failed intakes...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_retry_failed_intakes_async())
        finally:
            loop.close()
        return result
    except Exception as e:
        logger.error(f"Intake retry failed: {e}")
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def reprocess_intake(self, intake_id: str, claim_token: Optional[str] = None):
    """Reprocess a failed intake from its stored attachments."""
    if get_db() is None:
        logger.error("Database not initialized for intake reprocessing")
        return {"status": "error", "message": "Database not initialized"}

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _reprocess_intake_async(intake_id, claim_token)
            )
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"Intake reprocessing failed: {e}")
        raise self.retry(exc=e)


async def _reprocess_intake_async(intake_id: str, claim_token: Optional[str] = None):
    """Run classification/routing again for an intake claimed by the retry pass."""
    from models.agentic import ContractIntake

    db = get_db()
    if db is None:
        return {"status": "error", "message": "Database not available"}
    doc = await db.contract_intakes.find_one(
        {"id": intake_id, "status": "received", "attachments_stored": {"$ne": False}},
        {"_id": 0},
    )
    if not doc or not doc.get("attachments"):
        return {"status": "skipped", "intake_id": intake_id}
    return await _process_stored_intake(
        ContractIntake(**doc), db, claim_token or doc.get("claim_token")
    )


async def _retry_failed_intakes_async():
    """Async implementation of failed intake retry.

    * ``failed`` intakes whose attachments are stored are reprocessed from the
      stored files. Intakes that failed while uploading have no stored bytes;
      their original task retries with the webhook payload instead.
    * Claimed intakes (``received``/``processing``/``classified``) whose claim
      lease expired have no live worker: stored ones are reprocessed, the rest
      are rolled back and left for manual review.
    Every claim is conditional on the observed status and lease, so
    overlapping passes cannot claim the same intake twice.
    """
    db = get_db()
    if db is None:
        return {"status": "error", "message": "Database not available"}

    try:
        now = datetime.now(timezone.utc)
        # Failed intakes from the last 24 hours that have stored attachments
        # (upload failures are retried by their own task), plus stale claims of
        # any age. Filtering happens in the query, before the batch limit.
        cutoff = (now - timedelta(hours=24)).isoformat()
        stale_before = (now - INTAKE_CLAIM_LEASE).isoformat()

        candidates = await db.contract_intakes.find(
            {
                "$or": [
                    {
                        "status": "failed",
                        "createdAt": {"$gte": cutoff},
                        "attachments_stored": {"$ne": False},
                        "attachments": {
                            "$elemMatch": {"storage_key": {"$nin": [None, ""]}}
                        },
                    },
                    {
                        "status": {"$in": ["received", "processing", "classified"]},
                        "claimed_at": {"$lte": stale_before},
                    },
                ],
            },
            {"_id": 0},
        ).to_list(50)

        retried = 0
        recovered = 0
        for intake in candidates:
            observed = {
                "id": intake["id"],
                "status": intake["status"],
                "claimed_at": intake.get("claimed_at"),
            }
            stored = intake.get("attachments_stored") is not False and any(
                a.get("storage_key") for a in intake.get("attachments") or []
            )
            attempts = intake.get("processing_attempts", 0)
            if stored and attempts < MAX_INTAKE_PROCESSING_ATTEMPTS:
                claim = _new_claim()
                claimed = await db.contract_intakes.update_one(
                    observed,
                    {
                        "$set": {
                            "status": "received",
                            **claim,
                            "error_message": None,
                            "processing_started_at": None,
                            "processing_completed_at": None,
                        },
                        "$inc": {"processing_attempts": 1},
                    },
                )
                if not claimed.modified_count:
                    continue
                try:
                    reprocess_intake.delay(intake["id"], claim["claim_token"])
                except Exception as exc:
                    await _mark_intake_failed(
                        db,
                        intake["id"],
                        f"Retry could not be queued ({type(exc).__name__})",
                        claim["claim_token"],
                    )
                    raise
                retried += 1
            elif intake["status"] != "failed" or stored:
                # Stale with no stored files, or out of attempts.
                if await _move_to_manual_review(db, intake, observed, stored):
                    recovered += 1

        logger.info(
            f"Queued {retried} failed intakes for retry; "
            f"{recovered} moved to manual review"
        )
        return {"status": "success", "retried": retried, "manual_review": recovered}

    except Exception as e:
        logger.error(f"Intake retry error: {e}")
        return {"status": "error", "message": str(e)}


async def _move_to_manual_review(db, intake: dict, observed: dict, stored: bool):
    """Give up on an intake that cannot be reprocessed automatically."""
    update = {
        "$set": {
            "status": "manual_review",
            "processing_completed_at": _now_iso(),
            "error_message": (
                "Processing did not complete after "
                f"{MAX_INTAKE_PROCESSING_ATTEMPTS} attempts."
                if stored
                else "Processing was interrupted before attachments were "
                "stored; the email must be resent."
            ),
        }
    }
    if not stored:
        update["$set"].update(
            {"attachments": [], "attachments_stored": False, "pending_storage_keys": []}
        )
    claimed = await db.contract_intakes.update_one(observed, update)
    if not claimed.modified_count:
        return False
    if not stored:
        # Objects uploaded before the interruption are no longer referenced.
        retained = await _rollback_uploads(intake.get("pending_storage_keys") or [])
        if retained:
            await db.contract_intakes.update_one(
                {"id": intake["id"]},
                {"$addToSet": {"orphaned_storage_keys": {"$each": retained}}},
            )
    return True


# Import timedelta
from datetime import timedelta
