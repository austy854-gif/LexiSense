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
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from celery import shared_task
import httpx
import PyPDF2
import io

logger = logging.getLogger(__name__)

# Database reference
_db = None


def set_database(database):
    """Set the database reference for tasks."""
    global _db
    _db = database


def get_db():
    """Get the database reference."""
    return _db


def verify_resend_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Resend webhook signature using HMAC-SHA256."""
    if not secret or not signature:
        return False
    
    expected = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
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


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def process_resend_webhook(self, payload: Dict[str, Any]):
    """Process incoming Resend webhook for contract intake."""
    if not _db:
        logger.error("Database not initialized for intake processing")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Processing Resend webhook for contract intake")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_process_resend_webhook_async(payload))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Resend webhook processing failed: {e}")
        raise self.retry(exc=e)


async def _process_resend_webhook_async(payload: Dict[str, Any]):
    """Async implementation of Resend webhook processing."""
    from models.agentic import ContractIntake, IntakeChannel, IntakeStatus
    from services.ai_service import classify_intake_contract
    from services.storage_service import upload_file_to_s3

    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        # Verify webhook signature
        webhook_secret = os.environ.get("RESEND_WEBHOOK_SECRET")
        if webhook_secret:
            # Get raw payload bytes for verification
            raw_payload = json.dumps(payload, separators=(',', ':')).encode('utf-8')
            signature = payload.get("signature", "")
            if not verify_resend_signature(raw_payload, signature, webhook_secret):
                logger.warning("Invalid Resend webhook signature")
                return {"status": "error", "message": "Invalid signature"}

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
                    extracted_text = file_content.decode('utf-8', errors='ignore')
                except Exception:
                    extracted_text = ""
            elif content_type in ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]:
                extracted_text = extract_text_from_docx(file_content)
            
            # Upload to S3
            storage_key = await upload_file_to_s3(
                file_content=file_content,
                filename=filename,
                organization_id=organization_id,
                content_type=content_type
            )
            
            processed_attachments.append({
                "filename": filename,
                "size": len(file_content),
                "mime_type": content_type,
                "storage_key": storage_key,
                "extracted_text": extracted_text[:5000] if extracted_text else "",  # Store first 5000 chars
            })
        
        intake.attachments = processed_attachments
        
        # Save initial intake record
        await db.contract_intakes.insert_one(intake.model_dump())
        
        # If no attachments, mark as failed
        if not processed_attachments:
            await db.contract_intakes.update_one(
                {"id": intake.id},
                {"$set": {
                    "status": IntakeStatus.FAILED,
                    "error_message": "No valid attachments found",
                    "processing_completed_at": datetime.now(timezone.utc).isoformat()
                }}
            )
            return {"status": "error", "message": "No valid attachments", "intake_id": intake.id}

        # Update status to processing
        await db.contract_intakes.update_one(
            {"id": intake.id},
            {"$set": {
                "status": IntakeStatus.PROCESSING,
                "processing_started_at": datetime.now(timezone.utc).isoformat()
            }}
        )

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
            extracted_text=extracted_text
        )
        
        # Update with classification
        await db.contract_intakes.update_one(
            {"id": intake.id},
            {"$set": {
                "status": IntakeStatus.CLASSIFIED,
                "classified_type": classification.get("contract_type"),
                "classified_counterparty": classification.get("counterparty"),
                "confidence": classification.get("confidence", 0.0),
                "classification_reasoning": classification.get("reasoning"),
                "risk_indicators": classification.get("risk_indicators", []),
            }}
        )

        # Auto-route to playbook if applicable
        playbook_id = await _find_applicable_playbook(
            organization_id, 
            classification.get("contract_type"),
            classification.get("counterparty"),
            db
        )
        
        if playbook_id:
            await db.contract_intakes.update_one(
                {"id": intake.id},
                {"$set": {"playbook_id": playbook_id}}
            )

        # Auto-assign to team/user based on rules
        assigned_to = await _auto_assign_intake(organization_id, classification, db)
        if assigned_to:
            await db.contract_intakes.update_one(
                {"id": intake.id},
                {"$set": {"assigned_to": assigned_to}}
            )

        # Mark as routed
        await db.contract_intakes.update_one(
            {"id": intake.id},
            {"$set": {
                "status": IntakeStatus.ROUTED,
                "processing_completed_at": datetime.now(timezone.utc).isoformat()
            }}
        )

        # Create contract record from intake (optional - could wait for human review)
        contract_id = await _create_contract_from_intake(intake, classification, db)
        
        if contract_id:
            await db.contract_intakes.update_one(
                {"id": intake.id},
                {"$set": {"created_contract_id": contract_id}}
            )

        logger.info(f"Intake processed successfully: {intake.id}")
        return {
            "status": "success",
            "intake_id": intake.id,
            "contract_id": contract_id,
            "classified_type": classification.get("contract_type"),
            "confidence": classification.get("confidence")
        }

    except Exception as e:
        logger.error(f"Intake processing error: {e}")
        if 'intake' in locals():
            await db.contract_intakes.update_one(
                {"id": intake.id},
                {"$set": {
                    "status": IntakeStatus.FAILED,
                    "error_message": str(e),
                    "processing_completed_at": datetime.now(timezone.utc).isoformat()
                }}
            )
        return {"status": "error", "message": str(e)}


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
        {"domain": domain, "verified": True},
        {"_id": 0, "organizationId": 1}
    )
    
    if domain_mapping:
        return domain_mapping["organizationId"]
    
    # Fallback: look up organization by domains array
    org = await db.organizations.find_one(
        {"domains": domain},
        {"_id": 0, "id": 1}
    )
    
    if org:
        return org["id"]
    
    # Fallback: find org with matching email in users
    user = await db.users.find_one(
        {"email": sender_email},
        {"_id": 0, "organizationId": 1}
    )
    
    if user:
        return user["organizationId"]
    
    return None


async def _find_applicable_playbook(org_id: str, contract_type: str, counterparty: str, db) -> Optional[str]:
    """Find an applicable playbook for the contract."""
    if not contract_type:
        return None
    
    playbook = await db.legal_playbooks.find_one({
        "organizationId": org_id,
        "is_active": True,
        "$or": [
            {"contract_types": contract_type},
            {"contract_types": []},
        ]
    }, {"_id": 0, "id": 1})
    
    if playbook:
        return playbook["id"]
    
    # Try default playbook
    default_playbook = await db.legal_playbooks.find_one({
        "organizationId": org_id,
        "is_default": True,
        "is_active": True
    }, {"_id": 0, "id": 1})
    
    return default_playbook["id"] if default_playbook else None


async def _auto_assign_intake(org_id: str, classification: Dict, db) -> Optional[str]:
    """Auto-assign intake to a user based on workload and expertise."""
    # Get admin/manager users
    users = await db.users.find({
        "organizationId": org_id,
        "role": {"$in": ["admin", "manager"]},
        "isActive": True
    }, {"_id": 0, "id": 1, "email": 1}).to_list(20)
    
    if not users:
        return None
    
    # Simple round-robin or least-loaded assignment
    # In production, would check current workload
    return users[0]["id"]


async def _create_contract_from_intake(intake: ContractIntake, classification: Dict, db) -> Optional[str]:
    """Create a contract record from processed intake."""
    from models.contract import Contract
    import uuid
    
    # Use first attachment for contract creation
    if not intake.attachments:
        return None
    
    attachment = intake.attachments[0]
    
    # Initialize workflow history with initial draft state
    initial_workflow_history = [{
        "fromStatus": None,
        "toStatus": "draft",
        "action": "created_from_intake",
        "userId": intake.assigned_to or intake.source_metadata.get("resend_id", "system"),
        "userEmail": intake.source_email,
        "comment": "Contract created from email intake",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]
    
    contract = Contract(
        organizationId=intake.organizationId,
        uploadedBy=intake.assigned_to or intake.source_metadata.get("resend_id", "system"),
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
    
    await db.contracts.insert_one(contract.model_dump())
    
    # Trigger risk assessment
    from services.risk_tasks import assess_contract_risk
    assess_contract_risk.delay(contract.id, force_refresh=True)
    
    # Trigger obligation extraction if active
    if classification.get("contract_type") in ["Service Agreement", "Lease", "Partnership", "Licensing", "Purchase Agreement"]:
        from services.obligation_tasks import extract_obligations_from_contract
        extract_obligations_from_contract.delay(contract.id, force_refresh=True)
    
    # Trigger playbook analysis if playbook assigned
    if intake.playbook_id:
        from services.playbook_tasks import analyze_contract_with_playbook
        analyze_contract_with_playbook.delay(contract.id, intake.playbook_id, auto_apply=False)
    
    return contract.id


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def retry_failed_intakes(self):
    """Retry failed intake processing."""
    if not _db:
        logger.error("Database not initialized for intake retry")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Retrying failed intakes...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_retry_failed_intakes_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Intake retry failed: {e}")
        raise self.retry(exc=e)


async def _retry_failed_intakes_async():
    """Async implementation of failed intake retry."""
    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        # Find failed intakes from last 24 hours
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        
        failed_intakes = await db.contract_intakes.find({
            "status": "failed",
            "createdAt": {"$gte": cutoff}
        }).to_list(50)
        
        retried = 0
        for intake in failed_intakes:
            # Reset status and reprocess
            await db.contract_intakes.update_one(
                {"id": intake["id"]},
                {"$set": {
                    "status": "received",
                    "error_message": None,
                    "processing_started_at": None,
                    "processing_completed_at": None
                }}
            )
            
            # Re-process
            payload = {
                "data": {
                    "from": intake.get("source_email"),
                    "subject": intake.get("source_metadata", {}).get("subject", ""),
                    "attachments": intake.get("attachments", []),
                    "id": intake.get("source_metadata", {}).get("resend_id"),
                },
                "created_at": intake.get("source_metadata", {}).get("received_at"),
            }
            process_resend_webhook.delay(payload)
            retried += 1

        logger.info(f"Queued {retried} failed intakes for retry")
        return {"status": "success", "retried": retried}

    except Exception as e:
        logger.error(f"Intake retry error: {e}")
        return {"status": "error", "message": str(e)}


# Import timedelta
from datetime import timedelta