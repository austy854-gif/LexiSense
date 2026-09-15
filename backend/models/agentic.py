"""
Agentic AI Platform Schemas for LexiSense
Five Core Capabilities:
1. Productized Contract Agents
2. Automated Risk Scoring Engine
3. Playbook-Driven Automated Redlining
4. Zero-Training Business Intake
5. Post-Signature Obligation Extraction
"""

from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, List, Dict, Any, Literal, Union
from datetime import datetime, timezone
from enum import Enum
import uuid


# =============================================================================
# ENUMS & CONSTANTS
# =============================================================================

class AgentType(str, Enum):
    """Types of productized contract agents."""
    EXPIRATION_MONITOR = "expiration_monitor"
    COMPLIANCE_CHECKER = "compliance_checker"
    RISK_ALERT = "risk_alert"
    OBLIGATION_TRACKER = "obligation_tracker"
    RENEWAL_NEGOTIATOR = "renewal_negotiator"
    CUSTOM = "custom"


class AgentStatus(str, Enum):
    """Agent execution status."""
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"


class RiskCategory(str, Enum):
    """Risk categories for scoring."""
    FINANCIAL = "financial"
    LEGAL = "legal"
    OPERATIONAL = "operational"
    REGULATORY = "regulatory"
    REPUTATIONAL = "reputational"
    STRATEGIC = "strategic"


class PlaybookRuleType(str, Enum):
    """Types of playbook rules."""
    MUST_HAVE = "must_have"           # Clause must exist
    MUST_NOT_HAVE = "must_not_have"   # Clause must not exist
    PREFERRED_LANGUAGE = "preferred_language"  # Preferred wording
    FALLBACK_LANGUAGE = "fallback_language"    # Acceptable alternative
    NEGOTIATION_BOUNDARY = "negotiation_boundary"  # Min/max values
    CONDITIONAL = "conditional"       # If X then Y


class ObligationType(str, Enum):
    """Types of post-signature obligations."""
    PAYMENT = "payment"
    DELIVERY = "delivery"
    REPORTING = "reporting"
    COMPLIANCE = "compliance"
    RENEWAL = "renewal"
    TERMINATION = "termination"
    INSURANCE = "insurance"
    INDEMNIFICATION = "indemnification"
    CONFIDENTIALITY = "confidentiality"
    CUSTOM = "custom"


class ObligationStatus(str, Enum):
    """Obligation tracking status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    OVERDUE = "overdue"
    WAIVED = "waived"
    DISPUTED = "disputed"


class IntakeChannel(str, Enum):
    """Contract intake channels."""
    EMAIL = "email"
    UPLOAD = "upload"
    API = "api"
    WEBHOOK = "webhook"


class IntakeStatus(str, Enum):
    """Intake processing status."""
    RECEIVED = "received"
    PROCESSING = "processing"
    CLASSIFIED = "classified"
    ROUTED = "routed"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"


# =============================================================================
# 1. PRODUCTIZED CONTRACT AGENTS
# =============================================================================

class AgentConfig(BaseModel):
    """Configuration for a contract agent."""
    model_config = ConfigDict(extra="allow")
    
    # Monitoring parameters
    check_interval_hours: int = 24
    lookback_days: int = 30
    alert_threshold: Optional[float] = None
    
    # Filter criteria
    contract_types: List[str] = Field(default_factory=list)
    risk_levels: List[str] = Field(default_factory=list)
    counterparties: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    
    # Action configuration
    auto_escalate: bool = False
    escalation_recipients: List[str] = Field(default_factory=list)
    create_tasks: bool = True
    send_notifications: bool = True
    
    # Custom logic (stored as JSON-serializable dict)
    custom_logic: Dict[str, Any] = Field(default_factory=dict)


class AgentExecutionLog(BaseModel):
    """Log entry for agent execution."""
    model_config = ConfigDict(extra="allow")
    
    executed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    contracts_checked: int = 0
    alerts_generated: int = 0
    actions_taken: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    duration_ms: int = 0
    status: AgentStatus = AgentStatus.COMPLETED


class ContractAgent(BaseModel):
    """Productized contract agent - autonomous monitor/actor."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organizationId: str
    createdBy: str
    name: str
    description: Optional[str] = None
    agentType: AgentType
    status: AgentStatus = AgentStatus.ACTIVE
    config: AgentConfig = Field(default_factory=AgentConfig)
    schedule: str = "0 9 * * *"  # Cron expression (default: daily 9 AM)
    lastRunAt: Optional[str] = None
    nextRunAt: Optional[str] = None
    executionHistory: List[AgentExecutionLog] = Field(default_factory=list)
    totalExecutions: int = 0
    totalAlertsGenerated: int = 0
    createdAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updatedAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ContractAgentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    agentType: AgentType
    config: Optional[AgentConfig] = None
    schedule: str = "0 9 * * *"


class ContractAgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[AgentStatus] = None
    config: Optional[AgentConfig] = None
    schedule: Optional[str] = None


class ContractAgentResponse(BaseModel):
    id: str
    organizationId: str
    createdBy: str
    name: str
    description: Optional[str] = None
    agentType: AgentType
    status: AgentStatus
    config: AgentConfig
    schedule: str
    lastRunAt: Optional[str] = None
    nextRunAt: Optional[str] = None
    totalExecutions: int
    totalAlertsGenerated: int
    createdAt: str
    updatedAt: str


# =============================================================================
# 2. AUTOMATED RISK SCORING ENGINE
# =============================================================================

class RiskFactor(BaseModel):
    """Individual risk factor contributing to overall score."""
    model_config = ConfigDict(extra="allow")
    
    category: RiskCategory
    factor: str
    description: str
    score: int = Field(ge=0, le=100)  # 0-100
    weight: float = Field(ge=0.0, le=1.0, default=1.0)
    evidence: Optional[str] = None  # Contract text excerpt
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RiskScoreBreakdown(BaseModel):
    """Detailed breakdown of risk score by category."""
    model_config = ConfigDict(extra="allow")
    
    financial: int = 0
    legal: int = 0
    operational: int = 0
    regulatory: int = 0
    reputational: int = 0
    strategic: int = 0
    
    # Weighted category scores
    weighted_scores: Dict[str, float] = Field(default_factory=dict)


class RiskAssessment(BaseModel):
    """Complete risk assessment for a contract."""
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contractId: str
    organizationId: str
    
    # Overall score
    overall_score: int = Field(ge=0, le=100)
    risk_level: Literal["critical", "high", "medium", "low", "minimal"]
    
    # Breakdown
    breakdown: RiskScoreBreakdown
    factors: List[RiskFactor] = Field(default_factory=list)
    
    # AI analysis metadata
    model_used: str = "gpt-5.2"
    analysis_version: str = "1.0"
    confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    
    # Trending
    previous_score: Optional[int] = None
    score_trend: Literal["improving", "stable", "degrading"] = "stable"
    
    # Recommendations
    top_risks: List[str] = Field(default_factory=list)
    mitigation_priorities: List[str] = Field(default_factory=list)
    
    # Timestamps
    assessed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    assessed_by: str = "automated"
    
    @field_validator("risk_level", mode="before")
    @classmethod
    def compute_risk_level(cls, v, info):
        if isinstance(v, str):
            return v
        # Auto-compute from overall_score if not provided
        score = info.data.get("overall_score", 0)
        if score >= 80:
            return "critical"
        elif score >= 60:
            return "high"
        elif score >= 40:
            return "medium"
        elif score >= 20:
            return "low"
        return "minimal"


class RiskAssessmentResponse(BaseModel):
    contractId: str
    overall_score: int
    risk_level: str
    breakdown: RiskScoreBreakdown
    factors: List[RiskFactor]
    top_risks: List[str]
    mitigation_priorities: List[str]
    assessed_at: str
    score_trend: str


# =============================================================================
# 3. PLAYBOOK-DRIVEN AUTOMATED REDLINING
# =============================================================================

class PlaybookClauseRule(BaseModel):
    """Individual rule within a playbook for a specific clause type."""
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    clause_name: str  # e.g., "limitation_of_liability", "indemnification", "termination"
    clause_type: Literal["standard", "custom"]
    rule_type: PlaybookRuleType
    
    # Rule definition
    description: str
    required: bool = True
    
    # For PREFERRED_LANGUAGE / FALLBACK_LANGUAGE
    preferred_text: Optional[str] = None
    fallback_texts: List[str] = Field(default_factory=list)
    
    # For NEGOTIATION_BOUNDARY
    min_value: Optional[Union[str, float, int]] = None
    max_value: Optional[Union[str, float, int]] = None
    unit: Optional[str] = None  # e.g., "days", "USD", "%"
    
    # For CONDITIONAL
    condition: Optional[str] = None  # Natural language condition
    then_rule_id: Optional[str] = None
    
    # Metadata
    jurisdiction: Optional[str] = None
    contract_types: List[str] = Field(default_factory=list)  # Applies to these contract types
    priority: int = 100  # Lower = higher priority
    tags: List[str] = Field(default_factory=list)


class LegalPlaybook(BaseModel):
    """Legal playbook containing clause-level rules for automated redlining."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organizationId: str
    createdBy: str
    name: str
    description: Optional[str] = None
    version: str = "1.0"
    is_active: bool = True
    is_default: bool = False
    
    # Playbook rules
    rules: List[PlaybookClauseRule] = Field(default_factory=list)
    
    # Applicability
    contract_types: List[str] = Field(default_factory=list)
    jurisdictions: List[str] = Field(default_factory=list)
    counterparty_types: List[str] = Field(default_factory=list)  # e.g., "vendor", "customer", "partner"
    
    # Settings
    auto_apply: bool = False  # Automatically apply redlines
    require_approval: bool = True  # Require human approval before applying
    
    # Version control
    parent_playbook_id: Optional[str] = None
    change_log: List[Dict[str, Any]] = Field(default_factory=list)
    
    createdAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updatedAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LegalPlaybookCreate(BaseModel):
    name: str
    description: Optional[str] = None
    rules: List[PlaybookClauseRule] = []
    contract_types: List[str] = []
    jurisdictions: List[str] = []
    counterparty_types: List[str] = []
    auto_apply: bool = False
    require_approval: bool = True


class LegalPlaybookUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    rules: Optional[List[PlaybookClauseRule]] = None
    contract_types: Optional[List[str]] = None
    jurisdictions: Optional[List[str]] = None
    counterparty_types: Optional[List[str]] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    auto_apply: Optional[bool] = None
    require_approval: Optional[bool] = None


class LegalPlaybookResponse(BaseModel):
    id: str
    organizationId: str
    createdBy: str
    name: str
    description: Optional[str] = None
    version: str
    is_active: bool
    is_default: bool
    rules_count: int
    contract_types: List[str]
    jurisdictions: List[str]
    createdAt: str
    updatedAt: str


class RedlineSuggestion(BaseModel):
    """A single redline suggestion from playbook analysis."""
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contractId: str
    playbookId: str
    ruleId: str
    
    # Location in contract
    clause_name: str
    clause_type: str
    original_text: str
    suggested_text: str
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    
    # Rule info
    rule_type: PlaybookRuleType
    description: str
    priority: int
    
    # Status
    status: Literal["pending", "accepted", "rejected", "modified"] = "pending"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_comment: Optional[str] = None
    
    # Metadata
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RedlineSession(BaseModel):
    """A redlining session for a contract against a playbook."""
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contractId: str
    playbookId: str
    organizationId: str
    initiated_by: str
    
    # Results
    suggestions: List[RedlineSuggestion] = Field(default_factory=list)
    total_suggestions: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    
    # Status
    status: Literal["analyzing", "ready_for_review", "in_review", "completed", "failed"] = "analyzing"
    
    # Output
    redlined_document: Optional[str] = None  # Full document with accepted changes
    diff_summary: Optional[str] = None
    
    # Timestamps
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


# =============================================================================
# 4. ZERO-TRAINING BUSINESS INTAKE
# =============================================================================

class EmailIntakeConfig(BaseModel):
    """Configuration for email-based contract intake."""
    model_config = ConfigDict(extra="allow")
    
    # Resend webhook settings
    webhook_url: str
    webhook_secret: Optional[str] = None
    
    # Email routing
    intake_email: str  # e.g., contracts@company.lexisense.app
    fallback_recipients: List[str] = Field(default_factory=list)
    
    # Classification
    auto_classify: bool = True
    default_contract_type: str = "General"
    default_counterparty_extraction: bool = True
    
    # Processing
    max_file_size_mb: int = 25
    allowed_extensions: List[str] = Field(default_factory=lambda: [".pdf", ".docx", ".txt"])
    require_manual_review_for: List[str] = Field(default_factory=list)  # High-risk types


class ContractIntake(BaseModel):
    """Incoming contract intake record."""
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organizationId: str
    
    # Source
    channel: IntakeChannel
    source_email: Optional[str] = None  # Sender email
    source_metadata: Dict[str, Any] = Field(default_factory=dict)  # Email headers, etc.
    
    # Files
    attachments: List[Dict[str, Any]] = Field(default_factory=list)  # filename, size, mime, storage_key
    
    # Classification (AI-determined)
    status: IntakeStatus = IntakeStatus.RECEIVED
    classified_type: Optional[str] = None
    classified_counterparty: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    classification_reasoning: Optional[str] = None
    
    # Routing
    assigned_to: Optional[str] = None  # User ID
    assigned_team: Optional[str] = None
    playbook_id: Optional[str] = None
    
    # Processing
    processing_started_at: Optional[str] = None
    processing_completed_at: Optional[str] = None
    error_message: Optional[str] = None
    
    # Output
    created_contract_id: Optional[str] = None
    
    # Timestamps
    received_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    createdAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updatedAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ContractIntakeResponse(BaseModel):
    id: str
    organizationId: str
    channel: IntakeChannel
    source_email: Optional[str] = None
    attachments: List[Dict[str, Any]]
    status: IntakeStatus
    classified_type: Optional[str] = None
    classified_counterparty: Optional[str] = None
    confidence: float
    assigned_to: Optional[str] = None
    created_contract_id: Optional[str] = None
    received_at: str


# =============================================================================
# 5. POST-SIGNATURE OBLIGATION EXTRACTION
# =============================================================================

class ExtractedObligation(BaseModel):
    """An obligation extracted from a signed contract."""
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contractId: str
    organizationId: str
    
    # Obligation details
    obligation_type: ObligationType
    title: str
    description: str
    clause_reference: Optional[str] = None  # Section/clause number
    original_text: str  # Exact text from contract
    
    # Parties
    obligated_party: str  # "our_party" or "counterparty" or specific entity
    beneficiary_party: str
    
    # Timing
    due_date: Optional[str] = None  # ISO date
    frequency: Optional[Literal["once", "daily", "weekly", "monthly", "quarterly", "annually", "custom"]] = "once"
    custom_schedule: Optional[str] = None  # Cron expression for custom
    
    # Financial
    amount: Optional[float] = None
    currency: Optional[str] = None
    payment_terms: Optional[str] = None
    
    # Conditions
    conditions: List[str] = Field(default_factory=list)  # Preconditions
    triggers: List[str] = Field(default_factory=list)  # Triggering events
    
    # Status tracking
    status: ObligationStatus = ObligationStatus.PENDING
    assigned_to: Optional[str] = None  # User responsible
    assigned_team: Optional[str] = None
    
    # Compliance
    evidence_required: bool = False
    evidence_description: Optional[str] = None
    evidence_submitted: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Alerts
    alert_before_days: List[int] = Field(default_factory=lambda: [30, 14, 7, 1])
    escalation_enabled: bool = True
    escalation_recipients: List[str] = Field(default_factory=list)
    
    # AI metadata
    extraction_confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    extraction_model: str = "gpt-5.2"
    extraction_version: str = "1.0"
    verified_by_human: bool = False
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    
    # Audit
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


class ObligationAlert(BaseModel):
    """Alert for upcoming or overdue obligation."""
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    obligationId: str
    contractId: str
    organizationId: str
    
    alert_type: Literal["upcoming", "overdue", "escalation", "completed"]
    days_until_due: Optional[int] = None
    message: str
    severity: Literal["info", "warning", "critical"] = "warning"
    
    # Delivery
    sent_via: List[str] = Field(default_factory=list)  # email, in_app, webhook
    sent_to: List[str] = Field(default_factory=list)
    sent_at: Optional[str] = None
    
    # Status
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None
    
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ObligationExtractionJob(BaseModel):
    """Job to extract obligations from a signed contract."""
    model_config = ConfigDict(extra="allow")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contractId: str
    organizationId: str
    initiated_by: str
    
    status: Literal["pending", "processing", "completed", "failed", "requires_review"] = "pending"
    
    # Results
    extracted_count: int = 0
    obligations: List[str] = Field(default_factory=list)  # Obligation IDs
    
    # Errors
    errors: List[str] = Field(default_factory=list)
    
    # Timing
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: int = 0
    
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# =============================================================================
# INDEXES & COLLECTION NAMES
# =============================================================================

COLLECTIONS = {
    "contract_agents": "contract_agents",
    "risk_assessments": "risk_assessments",
    "legal_playbooks": "legal_playbooks",
    "redline_sessions": "redline_sessions",
    "contract_intakes": "contract_intakes",
    "extracted_obligations": "extracted_obligations",
    "obligation_alerts": "obligation_alerts",
    "obligation_extraction_jobs": "obligation_extraction_jobs",
}

# Recommended indexes for each collection
RECOMMENDED_INDEXES = {
    "contract_agents": [
        [("organizationId", 1), ("status", 1)],
        [("organizationId", 1), ("agentType", 1)],
        [("nextRunAt", 1)],
    ],
    "risk_assessments": [
        [("contractId", 1), ("assessed_at", -1)],
        [("organizationId", 1), ("overall_score", -1)],
        [("organizationId", 1), ("risk_level", 1)],
    ],
    "legal_playbooks": [
        [("organizationId", 1), ("is_active", 1)],
        [("organizationId", 1), ("is_default", 1)],
        [("contract_types", 1)],
    ],
    "redline_sessions": [
        [("contractId", 1), ("status", 1)],
        [("organizationId", 1), ("initiated_by", 1)],
    ],
    "contract_intakes": [
        [("organizationId", 1), ("status", 1)],
        [("organizationId", 1), ("received_at", -1)],
        [("source_email", 1)],
        [("created_contract_id", 1)],
    ],
    "extracted_obligations": [
        [("contractId", 1)],
        [("organizationId", 1), ("status", 1)],
        [("organizationId", 1), ("due_date", 1)],
        [("assigned_to", 1), ("status", 1)],
        [("obligation_type", 1)],
    ],
    "obligation_alerts": [
        [("obligationId", 1)],
        [("organizationId", 1), ("alert_type", 1)],
        [("sent_at", 1)],
    ],
    "obligation_extraction_jobs": [
        [("contractId", 1)],
        [("organizationId", 1), ("status", 1)],
    ],
}


# =============================================================================
# AGENT TASK PIPELINE CONFIGURATION
# =============================================================================

class TaskPipelineConfig(BaseModel):
    """Configuration for the async task execution pipeline."""
    
    # Celery settings (for production)
    use_celery: bool = True
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    
    # Queue names
    high_priority_queue: str = "high_priority"
    default_queue: str = "default"
    scheduled_queue: str = "scheduled"
    low_priority_queue: str = "low_priority"
    
    # Task timeouts (seconds)
    default_timeout: int = 300
    risk_scoring_timeout: int = 120
    redlining_timeout: int = 180
    obligation_extraction_timeout: int = 240
    
    # Retry policy
    max_retries: int = 3
    retry_delay: int = 60
    
    # Concurrency
    worker_concurrency: int = 4
    scheduled_worker_concurrency: int = 1


# Default pipeline config
DEFAULT_PIPELINE_CONFIG = TaskPipelineConfig()


# =============================================================================
# API REQUEST/RESPONSE MODELS
# =============================================================================

# Risk Scoring API
class TriggerRiskAssessmentRequest(BaseModel):
    contractId: str
    force_refresh: bool = False


# Playbook API
class AnalyzeContractWithPlaybookRequest(BaseModel):
    contractId: str
    playbookId: str
    auto_apply: bool = False


class ApplyRedlinesRequest(BaseModel):
    sessionId: str
    suggestionIds: List[str]  # IDs of suggestions to accept


# Intake API
class ProcessIntakeRequest(BaseModel):
    intakeId: str
    assign_to: Optional[str] = None
    playbook_id: Optional[str] = None


# Obligations API
class ExtractObligationsRequest(BaseModel):
    contractId: str
    force_refresh: bool = False


class UpdateObligationStatusRequest(BaseModel):
    obligationId: str
    status: ObligationStatus
    evidence: Optional[List[Dict[str, Any]]] = None
    comment: Optional[str] = None


# Agent API
class TriggerAgentRunRequest(BaseModel):
    agentId: str


class AgentRunResponse(BaseModel):
    agentId: str
    executionId: str
    status: AgentStatus
    contracts_checked: int
    alerts_generated: int
    started_at: str