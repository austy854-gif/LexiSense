"""
Celery tasks for Automated Risk Scoring Engine.
Handles risk assessment generation, refresh, and trending.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from celery import shared_task
import json

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


# Risk scoring weights by category
RISK_CATEGORY_WEIGHTS = {
    "financial": 0.25,
    "legal": 0.20,
    "operational": 0.20,
    "regulatory": 0.15,
    "reputational": 0.10,
    "strategic": 0.10,
}

# Risk keywords and their base scores (0-100)
RISK_KEYWORDS = {
    "financial": {
        "unlimited liability": 90,
        "uncapped damages": 85,
        "penalty": 70,
        "liquidated damages": 65,
        "indemnif": 75,
        "warranty": 50,
        "revenue share": 40,
        "payment terms": 30,
        "price adjustment": 35,
    },
    "legal": {
        "governing law": 30,
        "jurisdiction": 30,
        "arbitration": 40,
        "litigation": 60,
        "force majeure": 35,
        "termination for convenience": 55,
        "material breach": 65,
        "ip ownership": 45,
        "confidential": 40,
        "non-compete": 50,
    },
    "operational": {
        "sla": 45,
        "service level": 45,
        "uptime": 40,
        "availability": 40,
        "support": 35,
        "maintenance": 30,
        "implementation": 40,
        "delivery": 35,
        "performance": 40,
        "dependency": 50,
    },
    "regulatory": {
        "gdpr": 60,
        "hipaa": 65,
        "sox": 55,
        "pci": 60,
        "compliance": 50,
        "audit": 45,
        "regulatory": 55,
        "license": 40,
        "permit": 40,
        "data protection": 55,
        "privacy": 50,
    },
    "reputational": {
        "publicity": 50,
        "press release": 45,
        "brand": 40,
        "reputation": 55,
        "reference": 30,
        "case study": 30,
        "logo": 25,
    },
    "strategic": {
        "exclusive": 60,
        "most favored": 55,
        "change of control": 65,
        "assignment": 45,
        "subcontract": 40,
        "partnership": 35,
        "joint venture": 50,
        "acquisition": 55,
    },
}


def calculate_risk_factors(contract_text: str) -> List[Dict[str, Any]]:
    """Calculate risk factors from contract text using keyword matching."""
    text_lower = contract_text.lower()
    factors = []
    
    for category, keywords in RISK_KEYWORDS.items():
        for keyword, base_score in keywords.items():
            if keyword in text_lower:
                # Count occurrences
                count = text_lower.count(keyword)
                # Boost score based on frequency (capped)
                frequency_boost = min(count * 5, 20)
                score = min(base_score + frequency_boost, 100)
                
                # Find evidence snippet
                idx = text_lower.find(keyword)
                start = max(0, idx - 100)
                end = min(len(contract_text), idx + len(keyword) + 100)
                evidence = contract_text[start:end].strip()
                
                factors.append({
                    "category": category,
                    "factor": keyword.replace("_", " ").title(),
                    "description": f"Contract contains '{keyword}' clause/term",
                    "score": score,
                    "weight": RISK_CATEGORY_WEIGHTS.get(category, 1.0),
                    "evidence": evidence,
                    "metadata": {"keyword": keyword, "count": count}
                })
    
    return factors


def compute_overall_score(factors: List[Dict[str, Any]]) -> int:
    """Compute weighted overall risk score from factors."""
    if not factors:
        return 10  # Base minimal risk
    
    category_scores = {}
    category_weights = {}
    
    for factor in factors:
        cat = factor["category"]
        score = factor["score"]
        weight = factor["weight"]
        
        if cat not in category_scores:
            category_scores[cat] = []
            category_weights[cat] = weight
        
        category_scores[cat].append(score)
    
    # Weighted average per category, then weighted across categories
    weighted_sum = 0
    total_weight = 0
    
    for cat, scores in category_scores.items():
        if scores:
            avg_score = sum(scores) / len(scores)
            cat_weight = RISK_CATEGORY_WEIGHTS.get(cat, 0.1)
            weighted_sum += avg_score * cat_weight
            total_weight += cat_weight
    
    if total_weight == 0:
        return 10
    
    return int(round(weighted_sum / total_weight))


def compute_breakdown(factors: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute score breakdown by category."""
    breakdown = {
        "financial": 0,
        "legal": 0,
        "operational": 0,
        "regulatory": 0,
        "reputational": 0,
        "strategic": 0,
        "weighted_scores": {}
    }
    
    category_scores = {}
    
    for factor in factors:
        cat = factor["category"]
        score = factor["score"]
        if cat not in category_scores:
            category_scores[cat] = []
        category_scores[cat].append(score)
    
    for cat, scores in category_scores.items():
        if scores:
            avg = int(round(sum(scores) / len(scores)))
            breakdown[cat] = avg
            breakdown["weighted_scores"][cat] = round(avg * RISK_CATEGORY_WEIGHTS.get(cat, 0.1), 2)
    
    return breakdown


def determine_risk_level(score: int) -> str:
    """Determine risk level from score."""
    if score >= 80:
        return "critical"
    elif score >= 60:
        return "high"
    elif score >= 40:
        return "medium"
    elif score >= 20:
        return "low"
    return "minimal"


def generate_recommendations(factors: List[Dict[str, Any]]) -> tuple:
    """Generate top risks and mitigation priorities."""
    # Sort factors by score descending
    sorted_factors = sorted(factors, key=lambda x: x["score"], reverse=True)
    
    top_risks = []
    mitigations = []
    
    for factor in sorted_factors[:5]:
        top_risks.append(f"{factor['category'].title()}: {factor['factor']} (score: {factor['score']})")
        
        cat = factor["category"]
        if cat == "financial":
            mitigations.append("Negotiate liability caps and payment terms")
        elif cat == "legal":
            mitigations.append("Review governing law and dispute resolution clauses")
        elif cat == "operational":
            mitigations.append("Define clear SLAs and performance metrics")
        elif cat == "regulatory":
            mitigations.append("Ensure compliance obligations are achievable")
        elif cat == "reputational":
            mitigations.append("Limit publicity and brand usage rights")
        elif cat == "strategic":
            mitigations.append("Restrict assignment and change of control provisions")
    
    # Deduplicate mitigations
    seen = set()
    unique_mitigations = []
    for m in mitigations:
        if m not in seen:
            seen.add(m)
            unique_mitigations.append(m)
    
    return top_risks, unique_mitigations


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def assess_contract_risk(self, contract_id: str, force_refresh: bool = False):
    """Assess risk for a single contract."""
    if not _db:
        logger.error("Database not initialized for risk assessment")
        return {"status": "error", "message": "Database not initialized"}

    logger.info(f"Assessing risk for contract: {contract_id}")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_assess_contract_risk_async(contract_id, force_refresh))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Risk assessment failed for {contract_id}: {e}")
        raise self.retry(exc=e)


async def _assess_contract_risk_async(contract_id: str, force_refresh: bool = False):
    """Async implementation of risk assessment."""
    from models.agentic import RiskAssessment, RiskFactor, RiskScoreBreakdown

    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        contract = await db.contracts.find_one({"id": contract_id}, {"_id": 0})
        if not contract:
            return {"status": "error", "message": "Contract not found"}

        # Check if assessment already exists and is recent
        if not force_refresh:
            existing = await db.risk_assessments.find_one(
                {"contractId": contract_id},
                sort=[("assessed_at", -1)]
            )
            if existing:
                # Check if less than 24 hours old
                assessed_at = datetime.fromisoformat(existing["assessed_at"].replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - assessed_at).total_seconds() < 86400:
                    return {"status": "skipped", "message": "Recent assessment exists", "assessment_id": existing["id"]}

        # Get contract text
        contract_text = contract.get("originalText", "")
        if not contract_text:
            # Try to get from storage
            storage_key = contract.get("storageKey")
            if storage_key:
                from services.storage_service import generate_presigned_url
                # In production, would fetch and extract text
                contract_text = f"[Contract: {contract.get('title', 'Unknown')}]"

        if not contract_text:
            return {"status": "error", "message": "No contract text available for analysis"}

        # Calculate risk factors
        factors_data = calculate_risk_factors(contract_text)
        factors = [RiskFactor(**f) for f in factors_data]
        
        # Compute scores
        overall_score = compute_overall_score(factors_data)
        breakdown_data = compute_breakdown(factors_data)
        breakdown = RiskScoreBreakdown(**breakdown_data)
        risk_level = determine_risk_level(overall_score)
        top_risks, mitigations = generate_recommendations(factors_data)
        
        # Get previous score for trending
        previous = await db.risk_assessments.find_one(
            {"contractId": contract_id},
            sort=[("assessed_at", -1)],
            skip=1
        )
        previous_score = previous.get("overall_score") if previous else None
        
        if previous_score:
            if overall_score < previous_score - 5:
                trend = "improving"
            elif overall_score > previous_score + 5:
                trend = "degrading"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # Create assessment
        assessment = RiskAssessment(
            contractId=contract_id,
            organizationId=contract["organizationId"],
            overall_score=overall_score,
            risk_level=risk_level,
            breakdown=breakdown,
            factors=factors,
            top_risks=top_risks,
            mitigation_priorities=unique_mitigations,
            previous_score=previous_score,
            score_trend=trend,
            confidence=0.85 if factors_data else 0.5,
        )

        await db.risk_assessments.insert_one(assessment.model_dump())
        
        # Update contract with risk level
        await db.contracts.update_one(
            {"id": contract_id},
            {"$set": {"riskLevel": risk_level, "updatedAt": datetime.now(timezone.utc).isoformat()}}
        )

        logger.info(f"Risk assessment completed for {contract_id}: {risk_level} ({overall_score})")
        return {
            "status": "success",
            "assessment_id": assessment.id,
            "overall_score": overall_score,
            "risk_level": risk_level,
            "factors_count": len(factors)
        }

    except Exception as e:
        logger.error(f"Risk assessment error: {e}")
        return {"status": "error", "message": str(e)}


@shared_task(bind=True, max_retries=1, default_retry_delay=300)
def refresh_stale_risk_assessments(self):
    """Refresh risk assessments older than 7 days for active contracts."""
    if not _db:
        logger.error("Database not initialized for risk refresh")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Refreshing stale risk assessments...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_refresh_stale_risk_assessments_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Stale risk refresh failed: {e}")
        raise self.retry(exc=e)


async def _refresh_stale_risk_assessments_async():
    """Async implementation of stale risk refresh."""
    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        # Find contracts with assessments older than 7 days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        
        # Get contracts that have old assessments
        pipeline = [
            {"$match": {"assessed_at": {"$lt": cutoff}}},
            {"$sort": {"contractId": 1, "assessed_at": -1}},
            {"$group": {"_id": "$contractId", "latest": {"$first": "$$ROOT"}}},
            {"$project": {"contractId": "$_id", "assessed_at": "$latest.assessed_at"}}
        ]
        
        stale_assessments = await db.risk_assessments.aggregate(pipeline).to_list(200)
        
        refreshed = 0
        for item in stale_assessments:
            contract_id = item["contractId"]
            # Check if contract is still active
            contract = await db.contracts.find_one({"id": contract_id, "status": {"$ne": "expired"}})
            if contract:
                assess_contract_risk.delay(contract_id, force_refresh=True)
                refreshed += 1

        logger.info(f"Queued {refreshed} stale risk assessments for refresh")
        return {"status": "success", "refreshed": refreshed}

    except Exception as e:
        logger.error(f"Stale risk refresh error: {e}")
        return {"status": "error", "message": str(e)}


@shared_task(bind=True, max_retries=1)
def bulk_assess_organization_contracts(self, organization_id: str):
    """Assess risk for all contracts in an organization."""
    if not _db:
        logger.error("Database not initialized for bulk risk assessment")
        return {"status": "error", "message": "Database not initialized"}

    logger.info(f"Bulk risk assessment for organization: {organization_id}")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_bulk_assess_organization_contracts_async(organization_id))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Bulk risk assessment failed: {e}")
        return {"status": "error", "message": str(e)}


async def _bulk_assess_organization_contracts_async(organization_id: str):
    """Async implementation of bulk risk assessment."""
    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        contracts = await db.contracts.find(
            {"organizationId": organization_id, "status": {"$ne": "expired"}},
            {"_id": 0, "id": 1}
        ).to_list(500)
        
        queued = 0
        for contract in contracts:
            assess_contract_risk.delay(contract["id"], force_refresh=False)
            queued += 1

        logger.info(f"Queued {queued} contracts for risk assessment")
        return {"status": "success", "queued": queued}

    except Exception as e:
        logger.error(f"Bulk risk assessment error: {e}")
        return {"status": "error", "message": str(e)}


# Import timedelta
from datetime import timedelta