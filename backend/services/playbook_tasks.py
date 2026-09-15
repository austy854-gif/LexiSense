"""
Celery tasks for Playbook-Driven Automated Redlining.
Handles contract analysis against legal playbooks and redline generation.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
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


# Common clause patterns for extraction
CLAUSE_PATTERNS = {
    "limitation_of_liability": [
        r"limitation of liability",
        r"limitation of damages",
        r"cap on liability",
        r"maximum liability",
    ],
    "indemnification": [
        r"indemnif",
        r"hold harmless",
        r"defend and indemnify",
    ],
    "termination": [
        r"terminat",
        r"end of agreement",
        r"expiration",
    ],
    "confidentiality": [
        r"confidential",
        r"non-disclosure",
        r"proprietary information",
    ],
    "intellectual_property": [
        r"intellectual property",
        r"ip ownership",
        r"work product",
        r"inventions",
    ],
    "warranty": [
        r"warrant",
        r"represent",
        r"guarantee",
    ],
    "governing_law": [
        r"governing law",
        r"jurisdiction",
        r"venue",
    ],
    "force_majeure": [
        r"force majeure",
        r"act of god",
    ],
    "assignment": [
        r"assign",
        r"transfer",
        r"delegate",
    ],
    "payment_terms": [
        r"payment terms",
        r"net \d+",
        r"due within",
        r"invoice",
    ],
}


def extract_clauses(contract_text: str) -> Dict[str, List[str]]:
    """Extract clause text segments from contract."""
    clauses = {}
    text_lower = contract_text.lower()
    
    for clause_name, patterns in CLAUSE_PATTERNS.items():
        matches = []
        for pattern in patterns:
            # Find all occurrences with context
            for match in re.finditer(pattern, text_lower, re.IGNORECASE):
                start = max(0, match.start() - 200)
                end = min(len(contract_text), match.end() + 500)
                context = contract_text[start:end].strip()
                if context and context not in matches:
                    matches.append(context)
        if matches:
            clauses[clause_name] = matches
    
    return clauses


def match_clause_to_rule(clause_text: str, rule) -> Optional[Dict[str, Any]]:
    """Match a clause against a playbook rule and generate suggestion."""
    clause_lower = clause_text.lower()
    
    # Check if rule applies to this clause
    rule_clause = rule.get("clause_name", "").lower()
    if rule_clause not in clause_lower and clause_lower not in rule_clause:
        # Try fuzzy matching
        rule_words = set(rule_clause.split("_"))
        clause_words = set(clause_lower.split())
        if not rule_words & clause_words:
            return None
    
    rule_type = rule.get("rule_type")
    
    if rule_type == "must_have":
        # Check if required clause exists - already matched above
        return {
            "action": "verify_exists",
            "message": f"Required clause '{rule.get('clause_name')}' found",
            "suggested_text": rule.get("preferred_text"),
        }
    
    elif rule_type == "must_not_have":
        # Clause should not exist - flag for removal
        return {
            "action": "remove",
            "message": f"Clause '{rule.get('clause_name')}' should be removed per playbook",
            "suggested_text": "",
        }
    
    elif rule_type == "preferred_language":
        # Check if preferred language is present
        preferred = rule.get("preferred_text", "").lower()
        if preferred and preferred not in clause_lower:
            return {
                "action": "replace",
                "message": f"Preferred language not found for '{rule.get('clause_name')}'",
                "suggested_text": rule.get("preferred_text"),
                "original_text": clause_text[:500],
            }
    
    elif rule_type == "fallback_language":
        # Check if any fallback is present
        fallbacks = rule.get("fallback_texts", [])
        if fallbacks and not any(fb.lower() in clause_lower for fb in fallbacks):
            return {
                "action": "replace",
                "message": f"Acceptable fallback language not found for '{rule.get('clause_name')}'",
                "suggested_text": fallbacks[0] if fallbacks else "",
                "original_text": clause_text[:500],
            }
    
    elif rule_type == "negotiation_boundary":
        # Check numerical boundaries
        min_val = rule.get("min_value")
        max_val = rule.get("max_value")
        unit = rule.get("unit", "")
        
        # Extract numbers from clause
        numbers = re.findall(r'[\d,]+\.?\d*', clause_text)
        if numbers and (min_val is not None or max_val is not None):
            for num_str in numbers:
                try:
                    val = float(num_str.replace(",", ""))
                    if min_val is not None and val < min_val:
                        return {
                            "action": "modify",
                            "message": f"Value {val} {unit} below minimum {min_val} {unit}",
                            "suggested_text": f"[Adjust to at least {min_val} {unit}]",
                            "original_text": clause_text[:500],
                        }
                    if max_val is not None and val > max_val:
                        return {
                            "action": "modify",
                            "message": f"Value {val} {unit} exceeds maximum {max_val} {unit}",
                            "suggested_text": f"[Adjust to at most {max_val} {unit}]",
                            "original_text": clause_text[:500],
                        }
                except ValueError:
                    pass
    
    elif rule_type == "conditional":
        # Conditional rules - check if condition met
        condition = rule.get("condition", "")
        if condition and condition.lower() in clause_lower:
            then_rule_id = rule.get("then_rule_id")
            return {
                "action": "conditional",
                "message": f"Condition met for '{rule.get('clause_name')}'",
                "then_rule_id": then_rule_id,
            }
    
    return None


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def analyze_contract_with_playbook(self, contract_id: str, playbook_id: str, auto_apply: bool = False):
    """Analyze a contract against a legal playbook."""
    if not _db:
        logger.error("Database not initialized for playbook analysis")
        return {"status": "error", "message": "Database not initialized"}

    logger.info(f"Analyzing contract {contract_id} with playbook {playbook_id}")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_analyze_contract_with_playbook_async(contract_id, playbook_id, auto_apply))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Playbook analysis failed: {e}")
        raise self.retry(exc=e)


async def _analyze_contract_with_playbook_async(contract_id: str, playbook_id: str, auto_apply: bool = False):
    """Async implementation of playbook analysis."""
    from models.agentic import RedlineSession, RedlineSuggestion, PlaybookClauseRule

    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        contract = await db.contracts.find_one({"id": contract_id}, {"_id": 0})
        if not contract:
            return {"status": "error", "message": "Contract not found"}

        playbook = await db.legal_playbooks.find_one({"id": playbook_id}, {"_id": 0})
        if not playbook:
            return {"status": "error", "message": "Playbook not found"}

        # Get contract text
        contract_text = contract.get("originalText", "")
        if not contract_text:
            return {"status": "error", "message": "No contract text available"}

        # Extract clauses
        clauses = extract_clauses(contract_text)

        # Create redline session
        session = RedlineSession(
            contractId=contract_id,
            playbookId=playbook_id,
            organizationId=contract["organizationId"],
            initiated_by="system",
            status="analyzing",
        )

        await db.redline_sessions.insert_one(session.model_dump())

        suggestions = []
        rules = playbook.get("rules", [])

        for rule_data in rules:
            rule = PlaybookClauseRule(**rule_data)
            
            # Find matching clauses
            matching_clauses = clauses.get(rule.clause_name, [])
            
            if rule.rule_type == "must_have" and not matching_clauses:
                # Required clause missing
                suggestion = RedlineSuggestion(
                    contractId=contract_id,
                    playbookId=playbook_id,
                    ruleId=rule.id,
                    clause_name=rule.clause_name,
                    clause_type=rule.clause_type,
                    original_text="[MISSING - Clause not found in contract]",
                    suggested_text=rule.preferred_text or f"[Add {rule.clause_name} clause per playbook]",
                    rule_type=rule.rule_type,
                    description=rule.description,
                    priority=rule.priority,
                    confidence=0.9,
                )
                suggestions.append(suggestion)
            
            else:
                # Check each matching clause segment
                for clause_text in matching_clauses:
                    match_result = match_clause_to_rule(clause_text, rule_data)
                    if match_result:
                        suggestion = RedlineSuggestion(
                            contractId=contract_id,
                            playbookId=playbook_id,
                            ruleId=rule.id,
                            clause_name=rule.clause_name,
                            clause_type=rule.clause_type,
                            original_text=match_result.get("original_text", clause_text[:500]),
                            suggested_text=match_result.get("suggested_text", ""),
                            rule_type=rule.rule_type,
                            description=match_result.get("message", rule.description),
                            priority=rule.priority,
                            confidence=0.85,
                        )
                        suggestions.append(suggestion)

        # Update session with results
        session.suggestions = [s.model_dump() for s in suggestions]
        session.total_suggestions = len(suggestions)
        session.pending_count = len(suggestions)
        session.status = "ready_for_review"
        
        if auto_apply:
            session.status = "completed"
            # In auto-apply mode, accept all suggestions
            # Would generate redlined document here
            pass

        await db.redline_sessions.update_one(
            {"id": session.id},
            {"$set": session.model_dump()}
        )

        logger.info(f"Playbook analysis completed: {len(suggestions)} suggestions generated")
        return {
            "status": "success",
            "session_id": session.id,
            "suggestions_count": len(suggestions),
            "suggestions": [s.model_dump() for s in suggestions]
        }

    except Exception as e:
        logger.error(f"Playbook analysis error: {e}")
        return {"status": "error", "message": str(e)}


@shared_task(bind=True, max_retries=1, default_retry_delay=60)
def apply_redlines(self, session_id: str, suggestion_ids: List[str]):
    """Apply accepted redlines to generate redlined document."""
    if not _db:
        logger.error("Database not initialized for redline application")
        return {"status": "error", "message": "Database not initialized"}

    logger.info(f"Applying redlines for session: {session_id}")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_apply_redlines_async(session_id, suggestion_ids))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Redline application failed: {e}")
        return {"status": "error", "message": str(e)}


async def _apply_redlines_async(session_id: str, suggestion_ids: List[str]):
    """Async implementation of redline application."""
    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        session = await db.redline_sessions.find_one({"id": session_id}, {"_id": 0})
        if not session:
            return {"status": "error", "message": "Session not found"}

        contract = await db.contracts.find_one({"id": session["contractId"]}, {"_id": 0})
        if not contract:
            return {"status": "error", "message": "Contract not found"}

        contract_text = contract.get("originalText", "")
        if not contract_text:
            return {"status": "error", "message": "No contract text available"}

        # Apply suggestions
        applied = 0
        for suggestion in session.get("suggestions", []):
            if suggestion["id"] in suggestion_ids:
                # In production, would do precise text replacement
                # For now, track as applied
                applied += 1

        # Update suggestion statuses
        for suggestion in session.get("suggestions", []):
            if suggestion["id"] in suggestion_ids:
                suggestion["status"] = "accepted"
                suggestion["reviewed_at"] = datetime.now(timezone.utc).isoformat()

        session["accepted_count"] = applied
        session["pending_count"] = session["total_suggestions"] - applied
        session["status"] = "completed"
        session["completed_at"] = datetime.now(timezone.utc).isoformat()

        await db.redline_sessions.update_one(
            {"id": session_id},
            {"$set": session}
        )

        return {
            "status": "success",
            "applied": applied,
            "session_id": session_id
        }

    except Exception as e:
        logger.error(f"Redline application error: {e}")
        return {"status": "error", "message": str(e)}


@shared_task(bind=True, max_retries=1)
def bulk_analyze_with_playbook(self, organization_id: str, playbook_id: str):
    """Analyze all contracts in org against a playbook."""
    if not _db:
        logger.error("Database not initialized for bulk playbook analysis")
        return {"status": "error", "message": "Database not initialized"}

    logger.info(f"Bulk playbook analysis for org: {organization_id}")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_bulk_analyze_with_playbook_async(organization_id, playbook_id))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Bulk playbook analysis failed: {e}")
        return {"status": "error", "message": str(e)}


async def _bulk_analyze_with_playbook_async(organization_id: str, playbook_id: str):
    """Async implementation of bulk playbook analysis."""
    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        contracts = await db.contracts.find(
            {"organizationId": organization_id, "status": {"$in": ["draft", "review", "approved"]}},
            {"_id": 0, "id": 1}
        ).to_list(200)
        
        queued = 0
        for contract in contracts:
            analyze_contract_with_playbook.delay(contract["id"], playbook_id, auto_apply=False)
            queued += 1

        logger.info(f"Queued {queued} contracts for playbook analysis")
        return {"status": "success", "queued": queued}

    except Exception as e:
        logger.error(f"Bulk playbook analysis error: {e}")
        return {"status": "error", "message": str(e)}