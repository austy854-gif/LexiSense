"""
Agentic AI Platform API Routes for LexiSense.
Endpoints for contract agents, risk scoring, playbooks, redlining, intake, and obligations.
"""
from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks, Request
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import logging
import os

from models.agentic import (
    ContractAgent, ContractAgentCreate, ContractAgentUpdate, ContractAgentResponse,
    RiskAssessment, RiskAssessmentResponse, TriggerRiskAssessmentRequest,
    LegalPlaybook, LegalPlaybookCreate, LegalPlaybookUpdate, LegalPlaybookResponse,
    RedlineSession, RedlineSuggestion, AnalyzeContractWithPlaybookRequest, ApplyRedlinesRequest,
    ContractIntake, ContractIntakeResponse, ProcessIntakeRequest,
    ExtractedObligation, ObligationAlert, ExtractObligationsRequest, UpdateObligationStatusRequest,
    AgentRunResponse, TriggerAgentRunRequest,
    COLLECTIONS, RECOMMENDED_INDEXES,
)
from utils.auth import get_current_user, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agentic", tags=["Agentic AI"])

db = None


def init_db(database):
    """Initialize database connection for this router."""
    global db
    db = database
    
    # Create indexes for new collections
    import asyncio
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # Schedule index creation
        asyncio.create_task(_create_indexes())
    else:
        loop.run_until_complete(_create_indexes())


async def _create_indexes():
    """Create recommended indexes for agentic collections."""
    for collection_name, indexes in RECOMMENDED_INDEXES.items():
        if collection_name in COLLECTIONS.values():
            try:
                for index_spec in indexes:
                    await db[collection_name].create_index(index_spec)
                logger.info(f"Created indexes for {collection_name}")
            except Exception as e:
                logger.warning(f"Index creation warning for {collection_name}: {e}")


# =============================================================================
# CONTRACT AGENTS ENDPOINTS
# =============================================================================

@router.post("/agents", response_model=ContractAgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    agent_data: ContractAgentCreate,
    current_user: dict = Depends(require_role("admin", "manager"))
):
    """Create a new contract agent."""
    agent = ContractAgent(
        organizationId=current_user["organizationId"],
        createdBy=current_user["sub"],
        **agent_data.model_dump()
    )
    
    # Calculate next run time
    from services.agent_tasks import cron_to_next_run
    agent.nextRunAt = cron_to_next_run(agent.schedule)
    if agent.nextRunAt:
        agent.nextRunAt = agent.nextRunAt.isoformat()
    
    await db.contract_agents.insert_one(agent.model_dump())
    
    return ContractAgentResponse(**agent.model_dump())


@router.get("/agents", response_model=List[ContractAgentResponse])
async def list_agents(
    status_filter: Optional[str] = None,
    agent_type: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """List all contract agents for the organization."""
    query = {"organizationId": current_user["organizationId"]}
    
    if status_filter:
        query["status"] = status_filter
    if agent_type:
        query["agentType"] = agent_type
    
    agents = await db.contract_agents.find(query, {"_id": 0}).sort("createdAt", -1).to_list(100)
    return [ContractAgentResponse(**a) for a in agents]


@router.get("/agents/{agent_id}", response_model=ContractAgentResponse)
async def get_agent(
    agent_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific contract agent."""
    agent = await db.contract_agents.find_one(
        {"id": agent_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return ContractAgentResponse(**agent)


@router.patch("/agents/{agent_id}", response_model=ContractAgentResponse)
async def update_agent(
    agent_id: str,
    agent_data: ContractAgentUpdate,
    current_user: dict = Depends(require_role("admin", "manager"))
):
    """Update a contract agent."""
    update_data = agent_data.model_dump(exclude_unset=True)
    update_data["updatedAt"] = datetime.now(timezone.utc).isoformat()
    
    # Recalculate next run if schedule changed
    if "schedule" in update_data:
        from services.agent_tasks import cron_to_next_run
        next_run = cron_to_next_run(update_data["schedule"])
        if next_run:
            update_data["nextRunAt"] = next_run.isoformat()
    
    result = await db.contract_agents.update_one(
        {"id": agent_id, "organizationId": current_user["organizationId"]},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    agent = await db.contract_agents.find_one(
        {"id": agent_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    return ContractAgentResponse(**agent)


@router.post("/agents/{agent_id}/trigger", response_model=AgentRunResponse)
async def trigger_agent(
    agent_id: str,
    current_user: dict = Depends(require_role("admin", "manager"))
):
    """Manually trigger an agent execution."""
    from services.agent_tasks import execute_contract_agent
    
    agent = await db.contract_agents.find_one(
        {"id": agent_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Queue execution
    task = execute_contract_agent.delay(agent_id)
    
    return AgentRunResponse(
        agentId=agent_id,
        executionId=task.id,
        status="queued",
        contracts_checked=0,
        alerts_generated=0,
        started_at=datetime.now(timezone.utc).isoformat()
    )


@router.delete("/agents/{agent_id}")
async def delete_agent(
    agent_id: str,
    current_user: dict = Depends(require_role("admin"))
):
    """Delete a contract agent."""
    result = await db.contract_agents.delete_one(
        {"id": agent_id, "organizationId": current_user["organizationId"]}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"message": "Agent deleted successfully"}


# =============================================================================
# RISK SCORING ENDPOINTS
# =============================================================================

@router.post("/risk/assess", response_model=RiskAssessmentResponse)
async def assess_risk(
    request: TriggerRiskAssessmentRequest,
    current_user: dict = Depends(get_current_user)
):
    """Trigger risk assessment for a contract."""
    from services.risk_tasks import assess_contract_risk
    
    # Verify contract belongs to organization
    contract = await db.contracts.find_one(
        {"id": request.contractId, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    
    # Queue assessment
    task = assess_contract_risk.delay(request.contractId, request.force_refresh)
    
    # Return latest assessment if exists
    existing = await db.risk_assessments.find_one(
        {"contractId": request.contractId},
        sort=[("assessed_at", -1)]
    )
    
    if existing and not request.force_refresh:
        return RiskAssessmentResponse(**existing)
    
    # Return pending response
    return RiskAssessmentResponse(
        contractId=request.contractId,
        overall_score=0,
        risk_level="pending",
        breakdown={},
        factors=[],
        top_risks=[],
        mitigation_priorities=[],
        assessed_at=datetime.now(timezone.utc).isoformat(),
        score_trend="stable"
    )


@router.get("/risk/{contract_id}", response_model=RiskAssessmentResponse)
async def get_risk_assessment(
    contract_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get latest risk assessment for a contract."""
    # Verify contract belongs to organization
    contract = await db.contracts.find_one(
        {"id": contract_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    
    assessment = await db.risk_assessments.find_one(
        {"contractId": contract_id},
        sort=[("assessed_at", -1)]
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Risk assessment not found")
    
    return RiskAssessmentResponse(**assessment)


@router.get("/risk/organization/summary")
async def get_organization_risk_summary(
    current_user: dict = Depends(get_current_user)
):
    """Get risk summary for all contracts in organization."""
    pipeline = [
        {"$match": {"organizationId": current_user["organizationId"]}},
        {"$sort": {"assessed_at": -1}},
        {"$group": {
            "_id": "$contractId",
            "latest": {"$first": "$$ROOT"}
        }},
        {"$replaceRoot": {"newRoot": "$latest"}},
        {"$group": {
            "_id": "$risk_level",
            "count": {"$sum": 1},
            "avg_score": {"$avg": "$overall_score"}
        }}
    ]
    
    results = await db.risk_assessments.aggregate(pipeline).to_list(20)
    
    summary = {
        "by_level": {},
        "total_assessed": 0,
        "average_score": 0
    }
    
    total = 0
    weighted_sum = 0
    for r in results:
        summary["by_level"][r["_id"]] = {
            "count": r["count"],
            "average_score": round(r["avg_score"], 1)
        }
        total += r["count"]
        weighted_sum += r["avg_score"] * r["count"]
    
    summary["total_assessed"] = total
    summary["average_score"] = round(weighted_sum / total, 1) if total > 0 else 0
    
    return summary


@router.post("/risk/bulk-assess")
async def bulk_assess_organization(
    current_user: dict = Depends(require_role("admin", "manager"))
):
    """Trigger risk assessment for all active contracts in organization."""
    from services.risk_tasks import bulk_assess_organization_contracts
    
    task = bulk_assess_organization_contracts.delay(current_user["organizationId"])
    return {"task_id": task.id, "status": "queued"}


# =============================================================================
# PLAYBOOK ENDPOINTS
# =============================================================================

@router.post("/playbooks", response_model=LegalPlaybookResponse, status_code=status.HTTP_201_CREATED)
async def create_playbook(
    playbook_data: LegalPlaybookCreate,
    current_user: dict = Depends(require_role("admin", "manager"))
):
    """Create a new legal playbook."""
    # If this is set as default, unset other defaults
    if playbook_data.is_default:
        await db.legal_playbooks.update_many(
            {"organizationId": current_user["organizationId"], "is_default": True},
            {"$set": {"is_default": False, "updatedAt": datetime.now(timezone.utc).isoformat()}}
        )
    
    playbook = LegalPlaybook(
        organizationId=current_user["organizationId"],
        createdBy=current_user["sub"],
        **playbook_data.model_dump()
    )
    
    await db.legal_playbooks.insert_one(playbook.model_dump())
    
    return LegalPlaybookResponse(
        **playbook.model_dump(),
        rules_count=len(playbook.rules)
    )


@router.get("/playbooks", response_model=List[LegalPlaybookResponse])
async def list_playbooks(
    is_active: Optional[bool] = True,
    contract_type: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """List all legal playbooks for the organization."""
    query = {"organizationId": current_user["organizationId"]}
    
    if is_active is not None:
        query["is_active"] = is_active
    if contract_type:
        query["contract_types"] = contract_type
    
    playbooks = await db.legal_playbooks.find(query, {"_id": 0}).sort("updatedAt", -1).to_list(100)
    
    return [
        LegalPlaybookResponse(
            **p,
            rules_count=len(p.get("rules", []))
        ) for p in playbooks
    ]


@router.get("/playbooks/{playbook_id}", response_model=LegalPlaybookResponse)
async def get_playbook(
    playbook_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific legal playbook."""
    playbook = await db.legal_playbooks.find_one(
        {"id": playbook_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")
    
    return LegalPlaybookResponse(
        **playbook,
        rules_count=len(playbook.get("rules", []))
    )


@router.patch("/playbooks/{playbook_id}", response_model=LegalPlaybookResponse)
async def update_playbook(
    playbook_id: str,
    playbook_data: LegalPlaybookUpdate,
    current_user: dict = Depends(require_role("admin", "manager"))
):
    """Update a legal playbook."""
    update_data = playbook_data.model_dump(exclude_unset=True)
    update_data["updatedAt"] = datetime.now(timezone.utc).isoformat()
    
    # Handle default flag
    if playbook_data.is_default:
        await db.legal_playbooks.update_many(
            {"organizationId": current_user["organizationId"], "is_default": True, "id": {"$ne": playbook_id}},
            {"$set": {"is_default": False, "updatedAt": datetime.now(timezone.utc).isoformat()}}
        )
    
    result = await db.legal_playbooks.update_one(
        {"id": playbook_id, "organizationId": current_user["organizationId"]},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Playbook not found")
    
    playbook = await db.legal_playbooks.find_one(
        {"id": playbook_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    return LegalPlaybookResponse(
        **playbook,
        rules_count=len(playbook.get("rules", []))
    )


@router.delete("/playbooks/{playbook_id}")
async def delete_playbook(
    playbook_id: str,
    current_user: dict = Depends(require_role("admin"))
):
    """Delete a legal playbook."""
    result = await db.legal_playbooks.delete_one(
        {"id": playbook_id, "organizationId": current_user["organizationId"]}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Playbook not found")
    return {"message": "Playbook deleted successfully"}


# =============================================================================
# REDLINING ENDPOINTS
# =============================================================================

@router.post("/redline/analyze", response_model=Dict[str, Any])
async def analyze_with_playbook(
    request: AnalyzeContractWithPlaybookRequest,
    current_user: dict = Depends(get_current_user)
):
    """Analyze a contract against a playbook."""
    from services.playbook_tasks import analyze_contract_with_playbook
    
    # Verify contract and playbook belong to organization
    contract = await db.contracts.find_one(
        {"id": request.contractId, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    
    playbook = await db.legal_playbooks.find_one(
        {"id": request.playbookId, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")
    
    # Queue analysis
    task = analyze_contract_with_playbook.delay(
        request.contractId, 
        request.playbookId, 
        request.auto_apply
    )
    
    return {"task_id": task.id, "status": "queued"}


@router.get("/redline/sessions/{session_id}", response_model=RedlineSession)
async def get_redline_session(
    session_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get redline session details."""
    session = await db.redline_sessions.find_one(
        {"id": session_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Redline session not found")
    return RedlineSession(**session)


@router.post("/redline/sessions/{session_id}/apply")
async def apply_redlines(
    session_id: str,
    request: ApplyRedlinesRequest,
    current_user: dict = Depends(require_role("admin", "manager"))
):
    """Apply accepted redlines."""
    from services.playbook_tasks import apply_redlines
    
    session = await db.redline_sessions.find_one(
        {"id": session_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    task = apply_redlines.delay(session_id, request.suggestionIds)
    return {"task_id": task.id, "status": "queued"}


@router.get("/redline/contract/{contract_id}/sessions")
async def get_contract_redline_sessions(
    contract_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get all redline sessions for a contract."""
    contract = await db.contracts.find_one(
        {"id": contract_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    
    sessions = await db.redline_sessions.find(
        {"contractId": contract_id},
        {"_id": 0}
    ).sort("started_at", -1).to_list(50)
    
    return [RedlineSession(**s) for s in sessions]


# =============================================================================
# INTAKE ENDPOINTS
# =============================================================================

@router.post("/intake/webhook")
async def resend_webhook(request: Request):
    """Resend webhook endpoint for email-based contract intake."""
    from services.intake_tasks import process_resend_webhook, verify_resend_signature
    
    # Get raw body for signature verification
    body = await request.body()
    payload = await request.json()
    
    # Verify webhook signature if secret configured
    webhook_secret = os.environ.get("RESEND_WEBHOOK_SECRET")
    if webhook_secret:
        # Resend sends signature in 'resend-signature' header
        signature = request.headers.get("resend-signature") or request.headers.get("Resend-Signature")
        if not signature:
            logger.warning("Resend webhook received without signature header")
            raise HTTPException(status_code=401, detail="Missing webhook signature")
        
        if not verify_resend_signature(body, signature, webhook_secret):
            logger.warning("Resend webhook signature verification failed")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    
    # Queue processing
    task = process_resend_webhook.delay(payload)
    
    return {"task_id": task.id, "status": "queued"}


@router.get("/intake", response_model=List[ContractIntakeResponse])
async def list_intakes(
    status_filter: Optional[str] = None,
    limit: int = 50,
    current_user: dict = Depends(get_current_user)
):
    """List contract intakes for the organization."""
    query = {"organizationId": current_user["organizationId"]}
    
    if status_filter:
        query["status"] = status_filter
    
    intakes = await db.contract_intakes.find(query, {"_id": 0}).sort("received_at", -1).limit(limit).to_list(limit)
    return [ContractIntakeResponse(**i) for i in intakes]


@router.get("/intake/{intake_id}", response_model=ContractIntakeResponse)
async def get_intake(
    intake_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific contract intake."""
    intake = await db.contract_intakes.find_one(
        {"id": intake_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found")
    return ContractIntakeResponse(**intake)


@router.post("/intake/{intake_id}/process")
async def process_intake(
    intake_id: str,
    request: ProcessIntakeRequest,
    current_user: dict = Depends(require_role("admin", "manager"))
):
    """Manually process an intake (assign, route to playbook)."""
    intake = await db.contract_intakes.find_one(
        {"id": intake_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found")
    
    update_data = {}
    if request.assign_to:
        update_data["assigned_to"] = request.assign_to
    if request.playbook_id:
        update_data["playbook_id"] = request.playbook_id
    if update_data:
        update_data["updatedAt"] = datetime.now(timezone.utc).isoformat()
        await db.contract_intakes.update_one(
            {"id": intake_id},
            {"$set": update_data}
        )
    
    return {"message": "Intake updated", "intake_id": intake_id}


# =============================================================================
# OBLIGATIONS ENDPOINTS
# =============================================================================

@router.post("/obligations/extract", response_model=Dict[str, Any])
async def extract_obligations(
    request: ExtractObligationsRequest,
    current_user: dict = Depends(get_current_user)
):
    """Extract obligations from a signed contract."""
    from services.obligation_tasks import extract_obligations_from_contract
    
    # Verify contract belongs to organization
    contract = await db.contracts.find_one(
        {"id": request.contractId, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    
    task = extract_obligations_from_contract.delay(request.contractId, request.force_refresh)
    return {"task_id": task.id, "status": "queued"}


@router.get("/obligations/contract/{contract_id}", response_model=List[ExtractedObligation])
async def get_contract_obligations(
    contract_id: str,
    status_filter: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get all obligations for a contract."""
    contract = await db.contracts.find_one(
        {"id": contract_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    
    query = {"contractId": contract_id}
    if status_filter:
        query["status"] = status_filter
    
    obligations = await db.extracted_obligations.find(query, {"_id": 0}).sort("due_date", 1).to_list(200)
    return [ExtractedObligation(**o) for o in obligations]


@router.get("/obligations/organization", response_model=List[ExtractedObligation])
async def get_organization_obligations(
    status_filter: Optional[str] = None,
    obligation_type: Optional[str] = None,
    assigned_to: Optional[str] = None,
    due_before: Optional[str] = None,
    limit: int = 100,
    current_user: dict = Depends(get_current_user)
):
    """Get all obligations for the organization with filters."""
    query = {"organizationId": current_user["organizationId"]}
    
    if status_filter:
        query["status"] = status_filter
    if obligation_type:
        query["obligation_type"] = obligation_type
    if assigned_to:
        query["assigned_to"] = assigned_to
    if due_before:
        query["due_date"] = {"$lte": due_before}
    
    obligations = await db.extracted_obligations.find(query, {"_id": 0}).sort("due_date", 1).limit(limit).to_list(limit)
    return [ExtractedObligation(**o) for o in obligations]


@router.patch("/obligations/{obligation_id}/status")
async def update_obligation_status(
    obligation_id: str,
    request: UpdateObligationStatusRequest,
    current_user: dict = Depends(get_current_user)
):
    """Update obligation status and evidence."""
    obligation = await db.extracted_obligations.find_one(
        {"id": obligation_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")
    
    update_data = {
        "status": request.status,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    if request.evidence is not None:
        update_data["evidence_submitted"] = request.evidence
        if not obligation.get("verified_by_human"):
            update_data["verified_by_human"] = True
            update_data["verified_by"] = current_user["sub"]
            update_data["verified_at"] = datetime.now(timezone.utc).isoformat()
    
    if request.status == "completed":
        update_data["completed_at"] = datetime.now(timezone.utc).isoformat()
    
    await db.extracted_obligations.update_one(
        {"id": obligation_id},
        {"$set": update_data}
    )
    
    return {"message": "Obligation updated", "obligation_id": obligation_id}


@router.get("/obligations/alerts", response_model=List[ObligationAlert])
async def get_obligation_alerts(
    acknowledged: Optional[bool] = None,
    severity: Optional[str] = None,
    limit: int = 50,
    current_user: dict = Depends(get_current_user)
):
    """Get obligation alerts for the organization."""
    query = {"organizationId": current_user["organizationId"]}
    
    if acknowledged is not None:
        query["acknowledged"] = acknowledged
    if severity:
        query["severity"] = severity
    
    alerts = await db.obligation_alerts.find(query, {"_id": 0}).sort("sent_at", -1).limit(limit).to_list(limit)
    return [ObligationAlert(**a) for a in alerts]


@router.post("/obligations/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Acknowledge an obligation alert."""
    alert = await db.obligation_alerts.find_one(
        {"id": alert_id, "organizationId": current_user["organizationId"]},
        {"_id": 0}
    )
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    await db.obligation_alerts.update_one(
        {"id": alert_id},
        {"$set": {
            "acknowledged": True,
            "acknowledged_by": current_user["sub"],
            "acknowledged_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    return {"message": "Alert acknowledged", "alert_id": alert_id}


@router.post("/obligations/bulk-extract")
async def bulk_extract_obligations(
    current_user: dict = Depends(require_role("admin", "manager"))
):
    """Extract obligations for all signed contracts in organization."""
    from services.obligation_tasks import bulk_extract_obligations
    
    task = bulk_extract_obligations.delay(current_user["organizationId"])
    return {"task_id": task.id, "status": "queued"}


# =============================================================================
# HEALTH & STATUS ENDPOINTS
# =============================================================================

@router.get("/health")
async def agentic_health():
    """Health check for agentic services."""
    checks = {}
    
    # Check Celery
    try:
        from services.celery_app import celery_app
        inspect = celery_app.control.inspect()
        stats = inspect.stats()
        checks["celery"] = {"status": "healthy" if stats else "degraded", "workers": len(stats) if stats else 0}
    except Exception as e:
        checks["celery"] = {"status": "unhealthy", "details": str(e)}
    
    # Check database collections
    try:
        for coll_name in COLLECTIONS.values():
            count = await db[coll_name].count_documents({})
            checks[f"collection_{coll_name}"] = {"status": "healthy", "count": count}
    except Exception as e:
        checks["collections"] = {"status": "unhealthy", "details": str(e)}
    
    return {
        "status": "healthy" if all(c.get("status") != "unhealthy" for c in checks.values()) else "degraded",
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }