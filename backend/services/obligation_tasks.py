"""
Celery tasks for Post-Signature Obligation Extraction.
Handles obligation extraction from signed contracts, alert generation, and tracking.
"""
import asyncio
import logging
import json
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from celery import shared_task

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


# Obligation extraction patterns
OBLIGATION_PATTERNS = {
    "payment": {
        "keywords": ["pay", "payment", "fee", "invoice", "compensat", "remunerat", "price", "cost", "amount due"],
        "date_patterns": [
            r"(?:within|on or before|by)\s+(\d+)\s*(day|week|month|year)s?",
            r"(?:due|payable)\s+(?:on|within)\s+(\d+)\s*(day|week|month|year)s?",
            r"net\s+(\d+)",
        ],
        "amount_patterns": [
            r"\$[\d,]+(?:\.\d{2})?",
            r"(?:USD|EUR|GBP)\s*[\d,]+(?:\.\d{2})?",
        ],
    },
    "delivery": {
        "keywords": ["deliver", "provide", "supply", "ship", "transfer", "hand over", "make available"],
        "date_patterns": [
            r"(?:by|on or before|within)\s+(\d+)\s*(day|week|month|year)s?",
            r"delivery\s+(?:date|schedule)",
        ],
    },
    "reporting": {
        "keywords": ["report", "notify", "inform", "update", "status", "certif", "attest", "disclose"],
        "date_patterns": [
            r"(?:monthly|quarterly|annual|yearly|weekly)\s+(?:report|update)",
            r"(?:within|by)\s+(\d+)\s*(day|week|month)s?\s+(?:of|after)",
        ],
    },
    "compliance": {
        "keywords": ["comply", "compliance", "adher", "conform", "meet standard", "regulat", "audit", "certif"],
        "date_patterns": [
            r"(?:annual|yearly)\s+(?:audit|review|certif)",
            r"(?:within|by)\s+(\d+)\s*(day|week|month)s?",
        ],
    },
    "renewal": {
        "keywords": ["renew", "extend", "auto-renew", "automatic renew", "roll over"],
        "date_patterns": [
            r"(?:at least|no later than)\s+(\d+)\s*(day|week|month)s?\s+(?:prior|before)",
            r"notice\s+(?:of|period)\s+(\d+)\s*(day|week|month)s?",
        ],
    },
    "termination": {
        "keywords": ["terminat", "cancel", "end agreement", "expire", "notice of termination"],
        "date_patterns": [
            r"(?:with|upon)\s+(\d+)\s*(day|week|month)s?\s+(?:written\s+)?notice",
            r"notice\s+period\s+(?:of\s+)?(\d+)\s*(day|week|month)s?",
        ],
    },
    "insurance": {
        "keywords": ["insur", "policy", "coverage", "indemnif", "additional insured"],
        "date_patterns": [
            r"(?:maintain|carry|provide)\s+(?:insurance|coverage)",
            r"certificate\s+of\s+insurance",
        ],
    },
    "indemnification": {
        "keywords": ["indemnif", "hold harmless", "defend", "reimburse"],
        "date_patterns": [],
    },
    "confidentiality": {
        "keywords": ["confidential", "non-disclosure", "proprietary", "trade secret", "ndas"],
        "date_patterns": [
            r"(?:for|period of)\s+(\d+)\s*(year|month)s?\s+(?:after|following)",
            r"surviv\w*\s+(\d+)\s*(year|month)s?",
        ],
    },
}


def extract_obligations_from_text(contract_text: str, contract_id: str, organization_id: str) -> List[Dict[str, Any]]:
    """Extract obligations from contract text using pattern matching."""
    obligations = []
    text_lower = contract_text.lower()
    
    # Split into sentences/paragraphs for context
    paragraphs = re.split(r'\n\s*\n|\. \s*', contract_text)
    
    for obligation_type, config in OBLIGATION_PATTERNS.items():
        keywords = config["keywords"]
        date_patterns = config["date_patterns"]
        amount_patterns = config.get("amount_patterns", [])
        
        # Find paragraphs containing keywords
        for para in paragraphs:
            para_lower = para.lower()
            if any(kw in para_lower for kw in keywords):
                # Extract due date
                due_date = None
                frequency = "once"
                
                for pattern in date_patterns:
                    match = re.search(pattern, para_lower)
                    if match:
                        try:
                            if len(match.groups()) >= 2:
                                num = int(match.group(1))
                                unit = match.group(2).rstrip('s')
                                if unit == "day":
                                    due_date = (datetime.now(timezone.utc) + timedelta(days=num)).date().isoformat()
                                elif unit == "week":
                                    due_date = (datetime.now(timezone.utc) + timedelta(weeks=num)).date().isoformat()
                                elif unit == "month":
                                    due_date = (datetime.now(timezone.utc) + timedelta(days=num*30)).date().isoformat()
                                elif unit == "year":
                                    due_date = (datetime.now(timezone.utc) + timedelta(days=num*365)).date().isoformat()
                                
                                # Determine frequency
                                if "monthly" in para_lower or "quarterly" in para_lower or "annual" in para_lower or "yearly" in para_lower:
                                    frequency = "monthly" if "monthly" in para_lower else "quarterly" if "quarterly" in para_lower else "annually"
                        except (ValueError, IndexError):
                            pass
                        break
                
                # Extract amount
                amount = None
                currency = "USD"
                for pattern in amount_patterns:
                    match = re.search(pattern, para)
                    if match:
                        try:
                            amt_str = match.group(0).replace("$", "").replace(",", "").replace("USD", "").replace("EUR", "").replace("GBP", "").strip()
                            amount = float(amt_str)
                            if "EUR" in match.group(0):
                                currency = "EUR"
                            elif "GBP" in match.group(0):
                                currency = "GBP"
                        except ValueError:
                            pass
                        break
                
                # Determine parties
                obligated_party = "counterparty"  # Default
                if any(w in para_lower for w in ["we shall", "we will", "our obligation", "company shall", "provider shall"]):
                    obligated_party = "our_party"
                elif any(w in para_lower for w in ["you shall", "you will", "your obligation", "client shall", "customer shall"]):
                    obligated_party = "counterparty"
                
                # Create obligation
                obligation = {
                    "contractId": contract_id,
                    "organizationId": organization_id,
                    "obligation_type": obligation_type,
                    "title": f"{obligation_type.replace('_', ' ').title()} Obligation",
                    "description": para[:500],
                    "clause_reference": None,
                    "original_text": para[:1000],
                    "obligated_party": obligated_party,
                    "beneficiary_party": "our_party" if obligated_party == "counterparty" else "counterparty",
                    "due_date": due_date,
                    "frequency": frequency,
                    "custom_schedule": None,
                    "amount": amount,
                    "currency": currency,
                    "payment_terms": None,
                    "conditions": [],
                    "triggers": [],
                    "status": "pending",
                    "assigned_to": None,
                    "assigned_team": None,
                    "evidence_required": obligation_type in ["payment", "delivery", "reporting", "compliance"],
                    "evidence_description": f"Proof of {obligation_type} completion" if obligation_type in ["payment", "delivery", "reporting", "compliance"] else None,
                    "evidence_submitted": [],
                    "alert_before_days": [30, 14, 7, 1],
                    "escalation_enabled": True,
                    "escalation_recipients": [],
                    "extraction_confidence": 0.75,
                    "extraction_model": "pattern_matching_v1",
                    "extraction_version": "1.0",
                    "verified_by_human": False,
                    "verified_by": None,
                    "verified_at": None,
                }
                obligations.append(obligation)
    
    return obligations


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def extract_obligations_from_contract(self, contract_id: str, force_refresh: bool = False):
    """Extract obligations from a signed contract."""
    if not _db:
        logger.error("Database not initialized for obligation extraction")
        return {"status": "error", "message": "Database not initialized"}

    logger.info(f"Extracting obligations from contract: {contract_id}")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_extract_obligations_from_contract_async(contract_id, force_refresh))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Obligation extraction failed for {contract_id}: {e}")
        raise self.retry(exc=e)


async def _extract_obligations_from_contract_async(contract_id: str, force_refresh: bool = False):
    """Async implementation of obligation extraction."""
    from models.agentic import ExtractedObligation, ObligationExtractionJob

    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        contract = await db.contracts.find_one({"id": contract_id}, {"_id": 0})
        if not contract:
            return {"status": "error", "message": "Contract not found"}

        # Check if already extracted
        if not force_refresh:
            existing_count = await db.extracted_obligations.count_documents({"contractId": contract_id})
            if existing_count > 0:
                return {"status": "skipped", "message": f"Already has {existing_count} obligations", "count": existing_count}

        # Create extraction job record
        job = ObligationExtractionJob(
            contractId=contract_id,
            organizationId=contract["organizationId"],
            initiated_by="system",
            status="processing",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        await db.obligation_extraction_jobs.insert_one(job.model_dump())

        # Get contract text
        contract_text = contract.get("originalText", "")
        if not contract_text:
            # Try to get from AI analysis
            ai_analysis = contract.get("aiAnalysis", {})
            if ai_analysis:
                contract_text = json.dumps(ai_analysis)
        
        if not contract_text:
            await db.obligation_extraction_jobs.update_one(
                {"id": job.id},
                {"$set": {"status": "failed", "errors": ["No contract text available"], "completed_at": datetime.now(timezone.utc).isoformat()}}
            )
            return {"status": "error", "message": "No contract text available"}

        # Extract obligations using pattern matching
        extracted = extract_obligations_from_text(contract_text, contract_id, contract["organizationId"])
        
        # Save obligations
        obligation_ids = []
        for obl_data in extracted:
            obligation = ExtractedObligation(**obl_data)
            await db.extracted_obligations.insert_one(obligation.model_dump())
            obligation_ids.append(obligation.id)

        # Update job
        duration_ms = int((datetime.now(timezone.utc) - datetime.fromisoformat(job.started_at.replace("Z", "+00:00"))).total_seconds() * 1000)
        await db.obligation_extraction_jobs.update_one(
            {"id": job.id},
            {
                "$set": {
                    "status": "completed",
                    "extracted_count": len(obligation_ids),
                    "obligations": obligation_ids,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": duration_ms
                }
            }
        )

        logger.info(f"Extracted {len(obligation_ids)} obligations from contract {contract_id}")
        return {
            "status": "success",
            "job_id": job.id,
            "extracted_count": len(obligation_ids),
            "obligation_ids": obligation_ids
        }

    except Exception as e:
        logger.error(f"Obligation extraction error: {e}")
        if 'job' in locals():
            await db.obligation_extraction_jobs.update_one(
                {"id": job.id},
                {"$set": {"status": "failed", "errors": [str(e)], "completed_at": datetime.now(timezone.utc).isoformat()}}
            )
        return {"status": "error", "message": str(e)}


@shared_task(bind=True, max_retries=1, default_retry_delay=300)
def send_daily_obligation_alerts(self):
    """Send daily alerts for upcoming/overdue obligations."""
    if not _db:
        logger.error("Database not initialized for obligation alerts")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Sending daily obligation alerts...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_send_daily_obligation_alerts_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Daily obligation alerts failed: {e}")
        raise self.retry(exc=e)


async def _send_daily_obligation_alerts_async():
    """Async implementation of daily obligation alerts."""
    from services.audit_service import send_notification
    from models.agentic import ObligationAlert, ObligationStatus

    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        now = datetime.now(timezone.utc)
        today = now.date()
        alerts_sent = 0

        # Find pending/in-progress obligations with due dates
        obligations = await db.extracted_obligations.find({
            "status": {"$in": ["pending", "in_progress"]},
            "due_date": {"$ne": None}
        }).to_list(1000)

        for obl in obligations:
            try:
                due_date = datetime.fromisoformat(obl["due_date"]).date()
                days_until = (due_date - today).days
                
                # Check if alert should be sent
                alert_days = obl.get("alert_before_days", [30, 14, 7, 1])
                
                # Check for upcoming alerts
                for alert_day in alert_days:
                    if days_until == alert_day:
                        # Check if already sent
                        existing = await db.obligation_alerts.find_one({
                            "obligationId": obl["id"],
                            "alert_type": "upcoming",
                            "days_until_due": alert_day
                        })
                        if existing:
                            continue
                        
                        # Send alert
                        await _send_obligation_alert(obl, "upcoming", alert_day, db)
                        alerts_sent += 1
                
                # Check for overdue
                if days_until < 0:
                    existing = await db.obligation_alerts.find_one({
                        "obligationId": obl["id"],
                        "alert_type": "overdue"
                    })
                    if not existing:
                        await _send_obligation_alert(obl, "overdue", days_until, db)
                        alerts_sent += 1
                        
                        # Update obligation status
                        await db.extracted_obligations.update_one(
                            {"id": obl["id"]},
                            {"$set": {"status": ObligationStatus.OVERDUE}}
                        )

            except Exception as e:
                logger.error(f"Error processing obligation {obl.get('id')}: {e}")

        logger.info(f"Daily obligation alerts sent: {alerts_sent}")
        return {"status": "success", "alerts_sent": alerts_sent}

    except Exception as e:
        logger.error(f"Daily obligation alerts error: {e}")
        return {"status": "error", "message": str(e)}


async def _send_obligation_alert(obligation: Dict, alert_type: str, days_until: int, db):
    """Send notification for obligation alert."""
    org_id = obligation["organizationId"]
    
    # Get assigned user or admins
    recipients = []
    if obligation.get("assigned_to"):
        recipients.append(obligation["assigned_to"])
    else:
        admins = await db.users.find(
            {"organizationId": org_id, "role": {"$in": ["admin", "manager"]}},
            {"_id": 0, "id": 1}
        ).to_list(20)
        recipients = [a["id"] for a in admins]
    
    if not recipients:
        return
    
    # Determine severity
    if alert_type == "overdue":
        severity = "critical"
        message = f"OBLIGATION OVERDUE: '{obligation.get('title')}' was due {abs(days_until)} days ago"
    elif days_until <= 1:
        severity = "critical"
        message = f"URGENT: '{obligation.get('title')}' due {'today' if days_until == 0 else 'tomorrow'}"
    elif days_until <= 7:
        severity = "warning"
        message = f"'{obligation.get('title')}' due in {days_until} days"
    else:
        severity = "info"
        message = f"'{obligation.get('title')}' due in {days_until} days"
    
    for user_id in recipients:
        # Create alert record
        alert = ObligationAlert(
            obligationId=obligation["id"],
            contractId=obligation["contractId"],
            organizationId=org_id,
            alert_type=alert_type,
            days_until_due=days_until if alert_type == "upcoming" else None,
            message=message,
            severity=severity,
            sent_via=["in_app"],
            sent_to=[user_id],
            sent_at=datetime.now(timezone.utc).isoformat(),
        )
        await db.obligation_alerts.insert_one(alert.model_dump())
        
        # Send notification
        await send_notification(
            organization_id=org_id,
            user_id=user_id,
            notification_type=f"obligation_{alert_type}",
            title=f"Obligation Alert: {obligation.get('title')}",
            message=message,
            resource_type="obligation",
            resource_id=obligation["id"]
        )


@shared_task(bind=True, max_retries=1)
def cleanup_completed_obligations(self):
    """Clean up old completed/waived obligations."""
    if not _db:
        logger.error("Database not initialized for obligation cleanup")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Cleaning up completed obligations...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_cleanup_completed_obligations_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Obligation cleanup failed: {e}")
        return {"status": "error", "message": str(e)}


async def _cleanup_completed_obligations_async():
    """Async implementation of obligation cleanup."""
    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        # Archive obligations completed more than 1 year ago
        cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        
        result = await db.extracted_obligations.update_many(
            {
                "status": {"$in": ["completed", "waived"]},
                "completed_at": {"$lt": cutoff}
            },
            {"$set": {"archived": True, "archived_at": datetime.now(timezone.utc).isoformat()}}
        )
        
        # Also clean up old alerts
        alert_cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        alert_result = await db.obligation_alerts.delete_many({
            "sent_at": {"$lt": alert_cutoff},
            "acknowledged": True
        })
        
        logger.info(f"Archived {result.modified_count} obligations, deleted {alert_result.deleted_count} old alerts")
        return {
            "status": "success",
            "archived": result.modified_count,
            "alerts_deleted": alert_result.deleted_count
        }

    except Exception as e:
        logger.error(f"Obligation cleanup error: {e}")
        return {"status": "error", "message": str(e)}


@shared_task(bind=True, max_retries=1)
def bulk_extract_obligations(self, organization_id: str):
    """Extract obligations for all signed contracts in an organization."""
    if not _db:
        logger.error("Database not initialized for bulk obligation extraction")
        return {"status": "error", "message": "Database not initialized"}

    logger.info(f"Bulk obligation extraction for org: {organization_id}")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_bulk_extract_obligations_async(organization_id))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Bulk obligation extraction failed: {e}")
        return {"status": "error", "message": str(e)}


async def _bulk_extract_obligations_async(organization_id: str):
    """Async implementation of bulk obligation extraction."""
    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        # Find active/expired contracts without obligations
        contracts = await db.contracts.find(
            {"organizationId": organization_id, "status": {"$in": ["active", "expired"]}},
            {"_id": 0, "id": 1}
        ).to_list(200)
        
        queued = 0
        for contract in contracts:
            # Check if already has obligations
            existing = await db.extracted_obligations.count_documents({"contractId": contract["id"]})
            if existing == 0:
                extract_obligations_from_contract.delay(contract["id"], force_refresh=False)
                queued += 1

        logger.info(f"Queued {queued} contracts for obligation extraction")
        return {"status": "success", "queued": queued}

    except Exception as e:
        logger.error(f"Bulk obligation extraction error: {e}")
        return {"status": "error", "message": str(e)}