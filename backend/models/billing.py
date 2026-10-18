from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime, timezone
import uuid

from utils.plans import get_trial_config


def _trial_upload_limit() -> int:
    return get_trial_config().upload_limit


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
    trialContractsLimit: int = Field(default_factory=_trial_upload_limit)
    #: Paid plan id (see utils.plans); None while on the trial.
    planId: Optional[str] = None
    #: When the subscription first went past_due in the current episode (UTC
    #: ISO-8601). Starts the payment-failure grace period; cleared on recovery.
    pastDueSince: Optional[str] = None
    createdAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updatedAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class OrganizationBillingCreate(BaseModel):
    organizationId: str


class CheckoutSessionRequest(BaseModel):
    # Either may be sent; both are validated against the server-side plan
    # config. Neither -> the default paid plan's default price.
    priceId: Optional[str] = None
    planId: Optional[str] = None
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
    trialContractsLimit: int = Field(default_factory=_trial_upload_limit)
    stripeCustomerId: Optional[str] = None
    stripeSubscriptionId: Optional[str] = None
    planId: Optional[str] = None
    #: Member cap for the org's current plan; None = unlimited.
    memberLimit: Optional[int] = None
    #: Derived access state (paid / trial / past_due_grace / past_due / ...).
    accessState: Optional[str] = None
    #: Past-due grace period (UTC ISO-8601); only set while past_due.
    pastDueSince: Optional[str] = None
    gracePeriodEndsAt: Optional[str] = None
    #: Non-blocking warning to show the user (e.g. during the grace period).
    warning: Optional[str] = None


class WebhookEvent(BaseModel):
    id: str
    type: str
    data: dict