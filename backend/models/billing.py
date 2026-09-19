from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime, timezone
import uuid


class SubscriptionTier(str):
    TRIAL = "trial"
    TEAM = "team"
    CANCELLED = "cancelled"


class BillingStatus(str):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"


class OrganizationBilling(BaseModel):
    """Billing information attached to an organization"""
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organizationId: str
    stripeCustomerId: Optional[str] = None
    stripeSubscriptionId: Optional[str] = None
    stripePriceId: Optional[str] = None
    subscriptionTier: str = SubscriptionTier.TRIAL
    billingStatus: str = BillingStatus.TRIALING
    trialStartDate: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    trialEndDate: Optional[str] = None
    currentPeriodStart: Optional[str] = None
    currentPeriodEnd: Optional[str] = None
    cancelAtPeriodEnd: bool = False
    cancelledAt: Optional[str] = None
    trialContractsUsed: int = 0
    trialContractsLimit: int = 3
    createdAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updatedAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class OrganizationBillingCreate(BaseModel):
    organizationId: str


class CheckoutSessionRequest(BaseModel):
    priceId: str
    successUrl: str
    cancelUrl: str


class CheckoutSessionResponse(BaseModel):
    sessionId: str
    url: str


class BillingPortalRequest(BaseModel):
    returnUrl: str


class BillingPortalResponse(BaseModel):
    url: str


class SubscriptionInfo(BaseModel):
    subscriptionTier: str
    billingStatus: str
    trialEndDate: Optional[str] = None
    currentPeriodEnd: Optional[str] = None
    cancelAtPeriodEnd: bool = False
    trialContractsUsed: int = 0
    trialContractsLimit: int = 3
    stripeCustomerId: Optional[str] = None
    stripeSubscriptionId: Optional[str] = None


class WebhookEvent(BaseModel):
    id: str
    type: str
    data: dict