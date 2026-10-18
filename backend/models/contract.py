from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Any, Dict
from datetime import datetime, timezone
import uuid


class ContractAnalysis(BaseModel):
    summary: Optional[str] = None
    parties: Optional[List[str]] = None
    keyTerms: Optional[List[str]] = None
    risks: Optional[List[Dict[str, Any]]] = None
    dates: Optional[Dict[str, str]] = None
    value: Optional[str] = None
    riskLevel: Optional[str] = None
    recommendations: Optional[List[str]] = None


class ContractCreate(BaseModel):
    title: str
    counterparty: Optional[str] = None
    contractType: Optional[str] = "General"


class Contract(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organizationId: str
    uploadedBy: str
    title: str
    counterparty: Optional[str] = None
    contractType: str = "General"
    status: str = "draft"
    value: Optional[str] = None
    effectiveDate: Optional[str] = None
    expiryDate: Optional[str] = None
    riskLevel: Optional[str] = None
    originalText: Optional[str] = None
    aiAnalysis: Optional[Dict[str, Any]] = None
    #: Analysis coverage: True when the text exceeded the analysis cap and only
    #: the first ``analyzedChars`` (~``approxPagesAnalyzed`` pages) were analysed.
    analysisPartial: Optional[bool] = None
    analyzedChars: Optional[int] = None
    totalChars: Optional[int] = None
    approxPagesAnalyzed: Optional[int] = None
    storageKey: Optional[str] = None
    fileName: Optional[str] = None
    fileSize: Optional[int] = None
    mimeType: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    workflowHistory: List[Dict[str, Any]] = Field(default_factory=list)
    approvedBy: Optional[str] = None
    approvedAt: Optional[str] = None
    createdAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updatedAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ContractResponse(BaseModel):
    id: str
    organizationId: str
    uploadedBy: str
    title: str
    counterparty: Optional[str] = None
    contractType: str
    status: str
    value: Optional[str] = None
    effectiveDate: Optional[str] = None
    expiryDate: Optional[str] = None
    riskLevel: Optional[str] = None
    aiAnalysis: Optional[Dict[str, Any]] = None
    aiAnalysisStatus: Optional[str] = None
    aiAnalysisError: Optional[str] = None
    analysisPartial: Optional[bool] = None
    analyzedChars: Optional[int] = None
    totalChars: Optional[int] = None
    approxPagesAnalyzed: Optional[int] = None
    fileName: Optional[str] = None
    fileSize: Optional[int] = None
    tags: List[str] = []
    createdAt: str
    updatedAt: str
    uploaderEmail: Optional[str] = None


class ContractUpdate(BaseModel):
    """Body model for PATCH /contracts/{id}.

    Using a body model (instead of bare query parameters) keeps the update
    payload out of URLs/logs and gives FastAPI a single validated schema.
    """
    title: Optional[str] = None
    counterparty: Optional[str] = None
    contractType: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = None
    changeReason: Optional[str] = None


class ChatMessage(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class ChatResponse(BaseModel):
    answer: str
    contractId: str
    #: True when the contract exceeded the chat context cap, so the answer is
    #: based only on the first ``contextChars`` (~``approxPagesInContext`` pages).
    contextPartial: bool = False
    contextChars: Optional[int] = None
    approxPagesInContext: Optional[int] = None
