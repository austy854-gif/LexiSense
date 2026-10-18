from fastapi import APIRouter, HTTPException, status, Depends, Request
from dataclasses import dataclass
from typing import Optional, Tuple, Union
from datetime import datetime, timezone, timedelta
import logging
import stripe

from models.billing import (
    OrganizationBilling,
    OrganizationBillingCreate,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    BillingPortalRequest,
    BillingPortalResponse,
    SubscriptionInfo,
    SubscriptionTier,
    BillingStatus,
    WebhookEvent
)
from utils.auth import get_current_user, require_role
from utils.env import get_env
from utils.plans import (
    DEFAULT_PAID_PLAN_ID,
    PlanConfig,
    get_past_due_grace_period,
    get_paid_plan,
    get_paid_plans,
    get_trial_config,
    plan_for_price_id,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["Billing"])

db = None
stripe_api_key = None

def init_db(database):
    global db, stripe_api_key
    db = database
    stripe_api_key = get_env("STRIPE_SECRET_KEY")
    if stripe_api_key:
        stripe.api_key = stripe_api_key
    else:
        logger.warning("STRIPE_SECRET_KEY not set - billing will not work")


# Plan limits, trial length and Stripe price IDs live in ``utils.plans`` (the
# single, env-driven plan config). Nothing below hard-codes a price or limit.

#: Stripe statuses that grant paid-plan entitlements. ``trialing`` is included
#: so a customer who has entered a card is never treated as an unpaid trial.
PAID_BILLING_STATUSES = frozenset({BillingStatus.ACTIVE, BillingStatus.TRIALING})

ACCESS_PAID = "paid"
ACCESS_TRIAL = "trial"
ACCESS_TRIAL_EXPIRED = "trial_expired"
ACCESS_PAST_DUE = "past_due"
#: Payment failed but still inside BILLING_PAST_DUE_GRACE_DAYS: access continues.
ACCESS_PAST_DUE_GRACE = "past_due_grace"
ACCESS_CANCELLED = "cancelled"
ACCESS_INACTIVE = "inactive"

MSG_TRIAL_EXPIRED = "Your free trial has ended. Please upgrade to continue using LexiSense."
MSG_PAST_DUE = (
    "Your last payment failed. Please update your payment method in the billing "
    "portal to continue using LexiSense."
)
MSG_CANCELLED = "Your subscription has been cancelled. Please resubscribe to continue using LexiSense."
MSG_INACTIVE = "Your subscription is not active. Please complete checkout or contact support."


@dataclass(frozen=True)
class Entitlement:
    """What an organisation may do right now, derived from its billing record."""
    state: str
    plan_id: Optional[str]
    can_use_ai: bool
    unlimited_uploads: bool
    #: Trial upload cap (only meaningful when ``state == ACCESS_TRIAL``).
    upload_limit: Optional[int]
    #: Member cap; ``None`` = unlimited.
    member_limit: Optional[int]
    #: User-facing reason when access is blocked; "" otherwise.
    message: str = ""
    #: Non-blocking user-facing warning (past-due grace period); "" otherwise.
    warning: str = ""
    #: End of the past-due grace period (only for past_due states).
    grace_ends_at: Optional[datetime] = None

    @property
    def is_paid(self) -> bool:
        return self.state == ACCESS_PAID


def _billing_get(billing: Union[OrganizationBilling, dict, None], key: str, default=None):
    if billing is None:
        return default
    if isinstance(billing, dict):
        return billing.get(key, default)
    return getattr(billing, key, default)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        logger.warning("Unparseable billing timestamp %r", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: datetime) -> str:
    """Canonical stored form for billing timestamps: UTC, whole seconds.

    One fixed format keeps stored ISO strings comparable as strings.
    """
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _iso_from_ts(ts: Optional[int]) -> Optional[str]:
    if ts is None:
        return None
    return _iso(datetime.fromtimestamp(int(ts), tz=timezone.utc))


def past_due_grace_ends_at(billing) -> Optional[datetime]:
    """End of the past-due grace period, or ``None`` if it can't be determined.

    Records that went past_due before ``pastDueSince`` was tracked have no
    start time; they get no grace (the pre-existing, blocking behaviour).
    """
    since = _parse_iso(_billing_get(billing, "pastDueSince"))
    if since is None:
        return None
    return since + get_past_due_grace_period()


def _grace_warning(grace_end: datetime, now: datetime) -> str:
    remaining = grace_end - now
    days = max(1, -(-remaining // timedelta(days=1)))  # ceil, at least 1
    unit = "day" if days == 1 else "days"
    return (
        "Your last payment failed. Please update your payment method in the billing "
        f"portal within {days} {unit} to keep using LexiSense."
    )


def _resolve_paid_plan(billing) -> PlanConfig:
    return (
        get_paid_plan(_billing_get(billing, "planId"))
        or plan_for_price_id(_billing_get(billing, "stripePriceId"))
        or get_paid_plans()[DEFAULT_PAID_PLAN_ID]
    )


def compute_entitlement(billing: Union[OrganizationBilling, dict, None], now: Optional[datetime] = None) -> Entitlement:
    """Single place that turns a billing record into entitlements.

    * Team tier with Stripe status ``active`` or ``trialing`` -> paid: unlimited
      uploads, AI access, the plan's member cap.
    * Team tier ``past_due`` -> still entitled (with a warning) for
      ``BILLING_PAST_DUE_GRACE_DAYS`` after ``pastDueSince``, then blocked.
    * Other non-paid Team statuses -> blocked (not relabelled as a trial).
    * Cancelled tier -> blocked.
    * Trial tier -> in-app trial: AI + capped uploads until ``trialEndDate``.
    """
    now = now or datetime.now(timezone.utc)
    trial = get_trial_config()
    tier = _billing_get(billing, "subscriptionTier", SubscriptionTier.TRIAL)
    billing_status = _billing_get(billing, "billingStatus", BillingStatus.TRIALING)

    if tier == SubscriptionTier.TEAM:
        plan = _resolve_paid_plan(billing)
        if billing_status in PAID_BILLING_STATUSES:
            return Entitlement(ACCESS_PAID, plan.id, True, True, plan.max_uploads, plan.max_members)
        if billing_status == BillingStatus.PAST_DUE:
            grace_end = past_due_grace_ends_at(billing)
            if grace_end is not None and now < grace_end:
                return Entitlement(
                    ACCESS_PAST_DUE_GRACE, plan.id, True, True, plan.max_uploads, plan.max_members,
                    warning=_grace_warning(grace_end, now), grace_ends_at=grace_end,
                )
            return Entitlement(
                ACCESS_PAST_DUE, plan.id, False, False, 0, plan.max_members, MSG_PAST_DUE,
                grace_ends_at=grace_end,
            )
        if billing_status == BillingStatus.CANCELLED:
            return Entitlement(ACCESS_CANCELLED, plan.id, False, False, 0, plan.max_members, MSG_CANCELLED)
        return Entitlement(ACCESS_INACTIVE, plan.id, False, False, 0, plan.max_members, MSG_INACTIVE)

    if tier == SubscriptionTier.CANCELLED:
        return Entitlement(ACCESS_CANCELLED, None, False, False, 0, trial.max_members, MSG_CANCELLED)

    trial_end = _parse_iso(_billing_get(billing, "trialEndDate"))
    if trial_end is not None and now > trial_end:
        return Entitlement(ACCESS_TRIAL_EXPIRED, None, False, False, 0, trial.max_members, MSG_TRIAL_EXPIRED)
    return Entitlement(ACCESS_TRIAL, None, True, False, trial.upload_limit, trial.max_members)


def _trial_limit_message(limit: int) -> str:
    return f"You've reached the trial limit of {limit} contract uploads. Please upgrade to continue."


async def get_or_create_billing(org_id: str) -> OrganizationBilling:
    """Get existing billing record or create a new one for trial"""
    billing = await db.organization_billing.find_one({"organizationId": org_id})
    if billing:
        return OrganizationBilling(**billing)

    trial = get_trial_config()
    trial_end = datetime.now(timezone.utc) + timedelta(days=trial.days)
    new_billing = OrganizationBilling(
        organizationId=org_id,
        subscriptionTier=SubscriptionTier.TRIAL,
        billingStatus=BillingStatus.TRIALING,
        trialEndDate=trial_end.isoformat(),
        trialContractsUsed=0,
        trialContractsLimit=trial.upload_limit,
    )
    await db.organization_billing.insert_one(new_billing.model_dump())
    return new_billing


async def get_entitlement(org_id: str) -> Entitlement:
    return compute_entitlement(await get_or_create_billing(org_id))


async def ensure_ai_access(org_id: str) -> Entitlement:
    """Raise 403 unless the org may spend AI credits (active trial or paid)."""
    entitlement = await get_entitlement(org_id)
    if not entitlement.can_use_ai:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=entitlement.message)
    return entitlement


async def check_trial_access(org_id: str) -> tuple[bool, str]:
    """Check if organization can upload contracts (trial limits).

    NOTE: This is a READ-only check. It is NOT a reservation. The actual
    quota reservation is performed atomically in `reserve_trial_upload_atomic`.
    Call this only for pre-flight UX messaging.
    """
    billing = await get_or_create_billing(org_id)
    entitlement = compute_entitlement(billing)
    if entitlement.unlimited_uploads:
        return True, ""
    if entitlement.state != ACCESS_TRIAL:
        return False, entitlement.message
    if billing.trialContractsUsed >= entitlement.upload_limit:
        return False, _trial_limit_message(entitlement.upload_limit)
    return True, ""


async def reserve_trial_upload_atomic(org_id: str) -> tuple[bool, str]:
    """Atomically reserve one trial upload slot.

    Uses `find_one_and_update` with a filter that requires
    `trialContractsUsed < <configured trial limit>` so concurrent requests
    cannot exceed the cap. Paid orgs (active *or* Stripe-trialing) bypass the
    counter entirely.

    Returns (True, "") on successful reservation, or (False, message) on
    trial expiry / quota exhaustion / inactive subscription. Caller MUST call
    `release_trial_upload` if the upload subsequently fails (e.g. S3 write or
    DB insert fails).
    """
    billing = await get_or_create_billing(org_id)
    entitlement = compute_entitlement(billing)

    # Paid orgs never consume trial quota.
    if entitlement.unlimited_uploads:
        return True, ""
    if entitlement.state != ACCESS_TRIAL:
        return False, entitlement.message

    limit = entitlement.upload_limit
    now = datetime.now(timezone.utc).isoformat()
    # Atomic conditional increment: only succeeds if we're strictly below the limit.
    result = await db.organization_billing.find_one_and_update(
        {
            "organizationId": org_id,
            "$or": [
                {"trialContractsUsed": {"$lt": limit}},
                {"trialContractsUsed": {"$exists": False}},
            ],
        },
        {
            "$inc": {"trialContractsUsed": 1},
            "$set": {"updatedAt": now},
        },
    )
    if result is None:
        return False, _trial_limit_message(limit)
    return True, ""


async def release_trial_upload(org_id: str) -> None:
    """Refund one trial upload slot. Used when an upload fails after reservation.

    Safe to call multiple times; will never take the counter below zero.
    Paid orgs never reserved a slot, so nothing is refunded for them.
    """
    billing = await db.organization_billing.find_one({"organizationId": org_id})
    if billing is not None and compute_entitlement(billing).unlimited_uploads:
        return
    now = datetime.now(timezone.utc).isoformat()
    await db.organization_billing.find_one_and_update(
        {
            "organizationId": org_id,
            "trialContractsUsed": {"$gt": 0},
        },
        {
            "$inc": {"trialContractsUsed": -1},
            "$set": {"updatedAt": now},
        },
    )


async def increment_trial_usage(org_id: str):
    """DEPRECATED. Kept for backwards compatibility only.

    Non-atomic. Do not call for new code paths — use
    `reserve_trial_upload_atomic` + `release_trial_upload` instead.
    """
    await db.organization_billing.update_one(
        {"organizationId": org_id},
        {"$inc": {"trialContractsUsed": 1}, "$set": {"updatedAt": datetime.now(timezone.utc).isoformat()}}
    )


STRIPE_CHECKOUT_ERROR_MESSAGE = (
    "We couldn't start checkout right now. Please try again in a few minutes, "
    "or contact support if the problem continues."
)
STRIPE_PORTAL_ERROR_MESSAGE = (
    "We couldn't open the billing portal right now. Please try again in a few "
    "minutes, or contact support if the problem continues."
)


def _log_stripe_error(operation: str, org_id: str, exc: Exception) -> None:
    """Log full Stripe error details server-side; users only see a generic message."""
    logger.error(
        "Stripe %s error for org %s: %s (type=%s, code=%s, http_status=%s, request_id=%s)",
        operation,
        org_id,
        getattr(exc, "user_message", None) or str(exc),
        type(exc).__name__,
        getattr(exc, "code", None),
        getattr(exc, "http_status", None),
        getattr(exc, "request_id", None),
    )


@router.get("/subscription", response_model=SubscriptionInfo)
async def get_subscription(current_user: dict = Depends(get_current_user)):
    """Get current organization's subscription status"""
    org_id = current_user["organizationId"]
    billing = await get_or_create_billing(org_id)
    entitlement = compute_entitlement(billing)
    is_trial_expired = entitlement.state == ACCESS_TRIAL_EXPIRED

    return SubscriptionInfo(
        subscriptionTier=billing.subscriptionTier,
        billingStatus=billing.billingStatus,
        trialEndDate=billing.trialEndDate if not is_trial_expired else None,
        currentPeriodEnd=billing.currentPeriodEnd,
        cancelAtPeriodEnd=billing.cancelAtPeriodEnd,
        trialContractsUsed=billing.trialContractsUsed,
        trialContractsLimit=get_trial_config().upload_limit,
        stripeCustomerId=billing.stripeCustomerId,
        stripeSubscriptionId=billing.stripeSubscriptionId,
        planId=entitlement.plan_id,
        memberLimit=entitlement.member_limit,
        accessState=entitlement.state,
        pastDueSince=billing.pastDueSince if billing.billingStatus == BillingStatus.PAST_DUE else None,
        gracePeriodEndsAt=_iso(entitlement.grace_ends_at) if entitlement.grace_ends_at else None,
        warning=entitlement.warning or None,
    )


def resolve_checkout_price(request: CheckoutSessionRequest) -> Tuple[PlanConfig, str]:
    """Map the client's plan/price choice onto a server-side allow-listed price.

    The client never chooses an arbitrary Stripe price: a ``priceId`` must be
    one of the IDs configured for a plan in ``utils.plans``, and a ``planId``
    resolves to that plan's default price.
    """
    if request.priceId:
        plan = plan_for_price_id(request.priceId)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown or unsupported price.",
            )
        if request.planId and request.planId != plan.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Price does not belong to the selected plan.",
            )
        return plan, request.priceId

    plan_id = request.planId or DEFAULT_PAID_PLAN_ID
    plan = get_paid_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown plan.")
    if not plan.default_price_id:
        logger.error("No Stripe price configured for plan %s", plan.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured. Please contact support.",
        )
    return plan, plan.default_price_id


@router.post("/checkout", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    request: CheckoutSessionRequest,
    current_user: dict = Depends(require_role("admin"))
):
    """Create Stripe Checkout session for subscription"""
    if not stripe_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured. Please contact support."
        )
    
    # Validate the plan/price BEFORE touching Stripe.
    plan, price_id = resolve_checkout_price(request)

    org_id = current_user["organizationId"]
    billing = await get_or_create_billing(org_id)
    
    try:
        # Create or get Stripe customer (inside the try so a Stripe failure here
        # also gets the friendly message instead of a raw 500).
        if not billing.stripeCustomerId:
            customer = stripe.Customer.create(
                email=current_user["email"],
                metadata={"organizationId": org_id}
            )
            billing.stripeCustomerId = customer.id
            await db.organization_billing.update_one(
                {"organizationId": org_id},
                {"$set": {"stripeCustomerId": customer.id, "updatedAt": datetime.now(timezone.utc).isoformat()}}
            )

        session = stripe.checkout.Session.create(
            customer=billing.stripeCustomerId,
            payment_method_types=["card"],
            line_items=[{
                "price": price_id,
                "quantity": 1,
            }],
            mode="subscription",
            success_url=request.successUrl,
            cancel_url=request.cancelUrl,
            # No Stripe trial: the in-app trial already covered evaluation, so
            # the customer is billed (and entitled) from checkout onwards.
            subscription_data={
                "metadata": {"organizationId": org_id, "planId": plan.id}
            },
            metadata={"organizationId": org_id, "planId": plan.id}
        )
        return CheckoutSessionResponse(sessionId=session.id, url=session.url)
    except stripe.StripeError as e:
        _log_stripe_error("checkout", org_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=STRIPE_CHECKOUT_ERROR_MESSAGE,
        )


@router.post("/portal", response_model=BillingPortalResponse)
async def create_billing_portal(
    request: BillingPortalRequest,
    current_user: dict = Depends(require_role("admin"))
):
    """Create Stripe Billing Portal session for subscription management"""
    if not stripe_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured. Please contact support."
        )
    
    org_id = current_user["organizationId"]
    billing = await get_or_create_billing(org_id)
    
    if not billing.stripeCustomerId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing account found. Please subscribe first."
        )
    
    try:
        session = stripe.billing_portal.Session.create(
            customer=billing.stripeCustomerId,
            return_url=request.returnUrl,
        )
        return BillingPortalResponse(url=session.url)
    except stripe.StripeError as e:
        _log_stripe_error("portal", org_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=STRIPE_PORTAL_ERROR_MESSAGE,
        )


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    if not stripe_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured"
        )
    
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = get_env("STRIPE_WEBHOOK_SECRET")
    
    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=500, detail="Webhook not configured")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError as e:
        logger.error(f"Invalid payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    # Handle the event
    event_type = event["type"]
    data = event["data"]["object"]
    # Stripe does not guarantee delivery order; the event creation time (Unix
    # seconds) is used to drop stale state changes and to date past_due.
    event_at = _event_created(event)
    
    logger.info(f"Received Stripe webhook: {event_type}")
    
    if event_type == "checkout.session.completed":
        await handle_checkout_completed(data, event_at)
    elif event_type == "customer.subscription.created":
        await handle_subscription_created(data, event_at)
    elif event_type == "customer.subscription.updated":
        await handle_subscription_updated(data, event_at)
    elif event_type == "customer.subscription.deleted":
        await handle_subscription_deleted(data, event_at)
    elif event_type == "invoice.payment_failed":
        await handle_payment_failed(data, event_at)
    elif event_type == "invoice.paid":
        await handle_invoice_paid(data, event_at)
    
    return {"received": True}


def _event_created(event) -> Optional[int]:
    try:
        created = event.get("created")
    except AttributeError:
        created = getattr(event, "created", None)
    try:
        return int(created) if created is not None else None
    except (TypeError, ValueError):
        return None


def _state_filter(org_id: str, event_at: Optional[int]) -> dict:
    """Match the org's billing record unless a newer Stripe event already won.

    ``stripeEventAt`` is the creation time of the last Stripe event whose state
    was applied. An older event delivered late must not overwrite it (e.g. a
    stale ``invoice.payment_failed`` arriving after ``invoice.paid``). Events
    created in the same second are applied in arrival order.
    """
    query = {"organizationId": org_id}
    if event_at is not None:
        query["$or"] = [
            {"stripeEventAt": {"$exists": False}},
            {"stripeEventAt": None},
            {"stripeEventAt": {"$lte": event_at}},
        ]
    return query


def _state_set(update: dict, event_at: Optional[int]) -> dict:
    update = dict(update)
    update["updatedAt"] = datetime.now(timezone.utc).isoformat()
    if event_at is not None:
        update["stripeEventAt"] = event_at
    return update


async def _apply_state(org_id: str, update: dict, event_at: Optional[int]) -> bool:
    result = await db.organization_billing.update_one(
        _state_filter(org_id, event_at), {"$set": _state_set(update, event_at)}
    )
    if result.matched_count == 0:
        logger.info(
            "Stripe event (created=%s) not applied for org %s: no billing record, "
            "or a newer event was already applied",
            event_at, org_id,
        )
        return False
    return True


def _past_due_since(current: Optional[dict], event_at: Optional[int]) -> str:
    """Start of the current past-due episode: keep the existing start, if any."""
    started = _iso_from_ts(event_at) or _iso(datetime.now(timezone.utc))
    if current and current.get("billingStatus") == BillingStatus.PAST_DUE:
        existing = _parse_iso(current.get("pastDueSince"))
        if existing is not None:
            return min(_iso(existing), started)
    return started


async def handle_checkout_completed(session, event_at: Optional[int] = None):
    """Handle successful checkout session"""
    org_id = session.get("metadata", {}).get("organizationId")
    if not org_id:
        return
    
    subscription_id = session.get("subscription")
    customer_id = session.get("customer")
    
    if subscription_id:
        subscription = stripe.Subscription.retrieve(subscription_id)
        await update_billing_from_subscription(org_id, subscription, customer_id, event_at)


async def handle_subscription_created(subscription, event_at: Optional[int] = None):
    """Handle subscription creation"""
    org_id = subscription.get("metadata", {}).get("organizationId")
    if not org_id:
        return
    customer_id = subscription.get("customer")
    await update_billing_from_subscription(org_id, subscription, customer_id, event_at)


async def _org_for_subscription(subscription) -> Optional[str]:
    org_id = subscription.get("metadata", {}).get("organizationId")
    if org_id:
        return org_id
    # Fall back to the customer -> org mapping.
    customer_id = subscription.get("customer")
    if customer_id:
        billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
        if billing:
            return billing["organizationId"]
    return None


async def handle_subscription_updated(subscription, event_at: Optional[int] = None):
    """Handle subscription updates"""
    org_id = await _org_for_subscription(subscription)
    if not org_id:
        return
    customer_id = subscription.get("customer")
    await update_billing_from_subscription(org_id, subscription, customer_id, event_at)


async def handle_subscription_deleted(subscription, event_at: Optional[int] = None):
    """Handle subscription cancellation"""
    org_id = await _org_for_subscription(subscription)
    if not org_id:
        return

    now = datetime.now(timezone.utc).isoformat()
    await _apply_state(org_id, {
        "subscriptionTier": SubscriptionTier.CANCELLED,
        "billingStatus": BillingStatus.CANCELLED,
        "cancelledAt": now,
        "stripeSubscriptionId": None,
        "pastDueSince": None,
    }, event_at)


async def handle_payment_failed(invoice, event_at: Optional[int] = None):
    """Handle failed payment: start (or continue) the past-due grace period."""
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
    if not billing:
        return
    # A failed *first* invoice leaves the subscription ``incomplete`` (reported
    # by customer.subscription.updated); the org is still on its in-app trial
    # and must not be shown as a past-due subscriber.
    if billing.get("subscriptionTier") != SubscriptionTier.TEAM:
        return

    await _apply_state(billing["organizationId"], {
        "billingStatus": BillingStatus.PAST_DUE,
        "pastDueSince": _past_due_since(billing, event_at),
    }, event_at)


async def handle_invoice_paid(invoice, event_at: Optional[int] = None):
    """Handle successful invoice payment"""
    customer_id = invoice.get("customer")
    if not customer_id:
        return
    billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
    if not billing:
        return
    
    # If was past_due, restore to active and end the grace period. The tier is
    # set too, so records written before past_due kept the Team tier are repaired.
    if billing.get("billingStatus") == BillingStatus.PAST_DUE:
        await _apply_state(billing["organizationId"], {
            "billingStatus": BillingStatus.ACTIVE,
            "subscriptionTier": SubscriptionTier.TEAM,
            "pastDueSince": None,
        }, event_at)


async def update_billing_from_subscription(
    org_id: str, subscription, customer_id: str, event_at: Optional[int] = None
):
    """Update billing record from Stripe subscription object"""
    status_map = {
        "active": BillingStatus.ACTIVE,
        "trialing": BillingStatus.TRIALING,
        "past_due": BillingStatus.PAST_DUE,
        "canceled": BillingStatus.CANCELLED,
        "incomplete": BillingStatus.INCOMPLETE,
        "incomplete_expired": BillingStatus.CANCELLED,
        "unpaid": BillingStatus.PAST_DUE,
    }
    
    stripe_status = subscription.get("status", "incomplete")
    billing_status = status_map.get(stripe_status, BillingStatus.INCOMPLETE)
    current = await db.organization_billing.find_one({"organizationId": org_id})
    
    # Determine tier. A past-due / unpaid subscriber is still a Team customer
    # with a payment problem -- it is NOT a free trial. Only a subscription
    # that never started (``incomplete``) leaves the org on its in-app trial.
    if billing_status in (BillingStatus.ACTIVE, BillingStatus.TRIALING, BillingStatus.PAST_DUE):
        tier = SubscriptionTier.TEAM
    elif billing_status == BillingStatus.CANCELLED:
        tier = SubscriptionTier.CANCELLED
    else:
        tier = SubscriptionTier.TRIAL

    price_id = _subscription_price_id(subscription)
    plan = plan_for_price_id(price_id)
    if price_id and plan is None:
        logger.warning(
            "Subscription %s uses Stripe price %s which is not in the plan config; "
            "falling back to the default plan's limits",
            subscription.get("id"), price_id,
        )

    current_period_end = None
    if subscription.get("current_period_end"):
        current_period_end = datetime.fromtimestamp(subscription["current_period_end"], tz=timezone.utc).isoformat()

    update = {
        "stripeCustomerId": customer_id,
        "stripeSubscriptionId": subscription.get("id"),
        "stripePriceId": price_id,
        "planId": plan.id if plan else None,
        "subscriptionTier": tier,
        "billingStatus": billing_status,
        "currentPeriodStart": datetime.fromtimestamp(subscription.get("current_period_start", 0), tz=timezone.utc).isoformat() if subscription.get("current_period_start") else None,
        "currentPeriodEnd": current_period_end,
        "cancelAtPeriodEnd": subscription.get("cancel_at_period_end", False),
        # Past-due grace period: starts at the first past_due report, kept
        # across repeated reports, cleared as soon as the subscription recovers.
        "pastDueSince": (
            _past_due_since(current, event_at) if billing_status == BillingStatus.PAST_DUE else None
        ),
    }
    # Only overwrite the in-app trial end when Stripe actually reports a trial
    # (legacy subscriptions). Writing None here would erase the in-app trial
    # expiry and give non-paying orgs an open-ended trial.
    if subscription.get("trial_end"):
        update["trialEndDate"] = datetime.fromtimestamp(subscription["trial_end"], tz=timezone.utc).isoformat()

    await _apply_state(org_id, update, event_at)


def _subscription_price_id(subscription) -> Optional[str]:
    try:
        items = (subscription.get("items") or {}).get("data") or []
        if not items:
            return None
        return (items[0].get("price") or {}).get("id")
    except (AttributeError, IndexError, TypeError):
        return None


@router.get("/plans")
async def get_plans():
    """Get available pricing plans (rendered from the server-side plan config)."""
    trial = get_trial_config()
    plans = [
        {
            "id": "trial",
            "name": "Free Trial",
            "price": 0,
            "interval": "month",
            "trialDays": trial.days,
            "uploadLimit": trial.upload_limit,
            "memberLimit": trial.max_members,
            "features": [
                f"{trial.days}-day free trial",
                f"Up to {trial.upload_limit} contract uploads",
                "AI contract analysis",
                "Contract repository",
                "Renewal alerts",
                "Basic dashboard"
            ],
            "cta": "Start Free Trial"
        }
    ]
    for plan in get_paid_plans().values():
        members = (
            f"Team collaboration (up to {plan.max_members} members)"
            if plan.max_members
            else "Team collaboration (unlimited members)"
        )
        uploads = (
            "Unlimited contract uploads"
            if plan.max_uploads is None
            else f"Up to {plan.max_uploads} contract uploads"
        )
        plans.append({
            "id": plan.id,
            "name": plan.name,
            "price": plan.price_monthly,
            "interval": plan.interval,
            "uploadLimit": plan.max_uploads,
            "memberLimit": plan.max_members,
            "features": [
                uploads,
                "AI contract analysis",
                "Contract repository",
                "Renewal alerts",
                "Advanced dashboard & analytics",
                members,
                "Role-based access control",
                "Approval workflows",
                "Contract templates library",
                "PDF export",
                "Audit logs",
                "Priority support"
            ],
            "cta": f"Upgrade to {plan.name.replace(' Plan', '')}",
            "priceId": plan.default_price_id,
        })
    return {"plans": plans}


# Export the check function for use in contracts route
__all__ = [
    "router",
    "check_trial_access",
    "compute_entitlement",
    "ensure_ai_access",
    "get_entitlement",
    "Entitlement",
    "reserve_trial_upload_atomic",
    "release_trial_upload",
    "increment_trial_usage",
    "get_or_create_billing",
    "init_db",
]
