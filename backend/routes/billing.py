from fastapi import APIRouter, HTTPException, status, Depends, Request
from typing import Optional
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
from utils.billing_access import (
    ACCESS_CANCELLED,
    ACCESS_GRACE,
    ACCESS_PAID,
    ACCESS_PAST_DUE,
    ACCESS_UNVERIFIED_PLAN,
    Entitlement,
    compute_entitlement,
    grace_ends_at,
    upload_gate,
)
from utils.feature_flags import billing_ui_enabled
from utils.policy import capability_map
from utils.env import get_env
from utils import plans as plan_config
from services.stripe_prices import display_price
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

# Shown verbatim in the upload toast. Do not put the trial length here: it is
# configuration, and user-facing copy must not state a number of days.
TRIAL_ENDED_MESSAGE = "Your free trial has ended. Upgrade to keep adding contracts."
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


TRIAL_DAYS = 7
TRIAL_CONTRACT_LIMIT = 25
CHECKOUT_PRICE_REJECTED = "This plan is not available for checkout."


def resolve_checkout_price(request: CheckoutSessionRequest) -> str:
    """Map the request to a server-approved Stripe price id, or raise 400.

    Only the six STRIPE_PRICE_* ids from utils.plans are accepted, whether the
    client names a plan + interval or (legacy) a price id. An empty allowlist
    fails closed.
    """
    allowed = plan_config.allowed_price_ids()
    if not allowed:
        logger.error(
            "Checkout rejected: Stripe price allowlist is empty (set %s)",
            ", ".join(plan_config.STRIPE_PRICE_ENV_VARS),
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CHECKOUT_PRICE_REJECTED)
    if request.planId:
        plan = plan_config.get_paid_plan(request.planId)
        price_id = plan.price_for_interval(request.interval) if plan else None
    else:
        price_id = (request.priceId or "").strip()
    if not price_id or price_id not in allowed:
        logger.warning(
            "Checkout rejected: price not in allowlist",
            extra={"plan_id": request.planId, "interval": request.interval, "price_id": request.priceId},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CHECKOUT_PRICE_REJECTED)
    return price_id


def has_used_in_app_trial(billing: OrganizationBilling) -> bool:
    """True once the org has started (or used) the in-app trial.

    get_or_create_billing creates every record as a trial with trialStartDate
    and trialEndDate set, so in practice any org reaching checkout has already
    had its trial and must not get a second one from Stripe.
    """
    return bool(billing.trialStartDate or billing.trialEndDate)


#: Stripe rejects a trial_end less than 48 hours away; keep a small margin.
STRIPE_MIN_TRIAL_END = timedelta(hours=48, minutes=5)


def checkout_subscription_data(billing: OrganizationBilling, org_id: str, now: Optional[datetime] = None) -> dict:
    """Subscription data for checkout.

    Never a second, fresh Stripe trial once the in-app trial has started. If
    in-app trial time remains (and Stripe's 48h minimum allows it), the
    remainder is carried over as ``trial_end`` so upgrading early loses nothing.
    """
    data: dict = {"metadata": {"organizationId": org_id}}
    if not has_used_in_app_trial(billing):
        # "No trial" is expressed by omitting the key; never send 0.
        data["trial_period_days"] = TRIAL_DAYS
        return data
    now = now or datetime.now(timezone.utc)
    if billing.subscriptionTier == SubscriptionTier.TRIAL and billing.trialEndDate:
        try:
            end = datetime.fromisoformat(billing.trialEndDate.replace("Z", "+00:00"))
        except ValueError:
            return data
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end - now >= STRIPE_MIN_TRIAL_END:
            data["trial_end"] = int(end.timestamp())
    return data


#: Statuses of a subscription that still exists and bills the customer.
LIVE_BILLING_STATUSES = frozenset({BillingStatus.ACTIVE, BillingStatus.TRIALING, BillingStatus.PAST_DUE})
LIVE_STRIPE_STATUSES = frozenset({"active", "trialing", "past_due", "unpaid"})
MSG_ALREADY_SUBSCRIBED = (
    "Your organization already has a subscription. Use the billing portal to manage or change it."
)


def has_live_subscription(billing: OrganizationBilling) -> bool:
    return billing.subscriptionTier == SubscriptionTier.TEAM and billing.billingStatus in LIVE_BILLING_STATUSES


def _stripe_has_live_subscription(customer_id: str) -> bool:
    subs = stripe.Subscription.list(customer=customer_id, status="all", limit=20)
    return any(sub.get("status") in LIVE_STRIPE_STATUSES for sub in (subs.get("data") or []))


async def member_limit_for_org(org_id: str) -> Optional[int]:
    """Seat cap for the org's current tier, from utils.plans.

    Live paid subscriptions get their plan's cap (looked up from the stored
    Stripe price). Everything else -- trial, cancelled, or a price that isn't
    configured -- gets the trial cap (fail closed to the smallest tier).
    """
    billing = await db.organization_billing.find_one({"organizationId": org_id}) or {}
    entitlement = compute_entitlement(billing)
    if entitlement.state == ACCESS_UNVERIFIED_PLAN:
        logger.error("Org %s has a subscription on an unknown Stripe price; applying trial seat cap", org_id)
    return entitlement.max_members


async def get_or_create_billing(org_id: str, database=None) -> OrganizationBilling:
    """Get existing billing record or create a new one for trial.

    ``database`` lets Celery workers (own Motor handle per task) share this.
    """
    database = db if database is None else database
    billing = await database.organization_billing.find_one({"organizationId": org_id})
    if billing:
        return OrganizationBilling(**billing)
    
    # Create new trial billing record
    trial_end = datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)
    new_billing = OrganizationBilling(
        organizationId=org_id,
        subscriptionTier=SubscriptionTier.TRIAL,
        billingStatus=BillingStatus.TRIALING,
        trialEndDate=trial_end.isoformat(),
        trialContractsUsed=0,
        trialContractsLimit=TRIAL_CONTRACT_LIMIT
    )
    try:
        await database.organization_billing.insert_one(new_billing.model_dump())
    except DuplicateKeyError:
        existing = await database.organization_billing.find_one({"organizationId": org_id})
        if existing:
            return OrganizationBilling(**existing)
        raise
    return new_billing


async def check_trial_access(org_id: str) -> tuple[bool, str]:
    """Check if organization can upload contracts (trial limits).

    NOTE: This is a READ-only check. It is NOT a reservation. The actual
    quota reservation is performed atomically in `reserve_trial_upload_atomic`.
    Call this only for pre-flight UX messaging.
    """
    billing = await get_or_create_billing(org_id)
    raw = await db.organization_billing.find_one({"organizationId": org_id}) or {}
    entitlement = compute_entitlement(raw)
    if entitlement.state in (ACCESS_PAID, ACCESS_GRACE):
        return True, ""
    if entitlement.blocked:
        return False, entitlement.message

    # Check contract upload limit
    if billing.trialContractsUsed >= billing.trialContractsLimit:
        return False, trial_limit_message()

    return True, ""


async def reserve_trial_upload_atomic(org_id: str) -> tuple[bool, str]:
    """Atomically reserve one trial upload slot.

    Uses `find_one_and_update` with a filter that requires
    `trialContractsUsed < trialContractsLimit` so concurrent requests cannot
    exceed the cap. Paid Team orgs bypass the counter entirely.

    Returns (True, "") on successful reservation, or (False, message) on
    trial expiry / quota exhaustion. Caller MUST call `release_trial_upload`
    if the upload subsequently fails (e.g. S3 write or DB insert fails).
    """
    # Ensure billing record exists so filter has stable fields.
    await get_or_create_billing(org_id)
    now = datetime.now(timezone.utc).isoformat()

    # First check for paid subscription -- paid orgs never consume trial quota.
    billing_doc = await db.organization_billing.find_one({"organizationId": org_id}) or {}
    entitlement = compute_entitlement(billing_doc)
    if entitlement.state in (ACCESS_PAID, ACCESS_GRACE):
        return True, ""
    if entitlement.blocked:
        return False, entitlement.message

    # Atomic conditional increment: only succeeds if we're strictly below the limit.
    result = await db.organization_billing.find_one_and_update(
        {
            "organizationId": org_id,
            "$expr": {"$lt": ["$trialContractsUsed", "$trialContractsLimit"]},
        },
        {
            "$inc": {"trialContractsUsed": 1},
            "$set": {"updatedAt": now},
        },
    )
    if result is None:
        return False, trial_limit_message()
    return True, ""


async def assert_org_writable(org_id: str) -> None:
    """Reject contract mutations once a subscription is read-only.

    Uploads already go through :func:`reserve_trial_upload_atomic`. Edits,
    deletes, restores and workflow changes use this so the billing page's
    read-only state is actually enforced.
    """
    await get_or_create_billing(org_id)
    raw = await db.organization_billing.find_one({"organizationId": org_id}) or {}
    entitlement = compute_entitlement(raw)
    if entitlement.state in (ACCESS_PAST_DUE, ACCESS_CANCELLED, ACCESS_UNVERIFIED_PLAN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=entitlement.message)


def trial_limit_message() -> str:
    step = "Upgrade to keep adding contracts." if billing_ui_enabled() else "Please contact support to continue."
    return f"You've used all your trial uploads. {step}"


async def get_entitlement(org_id: str, database=None) -> Entitlement:
    database = db if database is None else database
    await get_or_create_billing(org_id, database)
    raw = await database.organization_billing.find_one({"organizationId": org_id}) or {}
    return compute_entitlement(raw)


async def ensure_ai_access(org_id: str) -> Entitlement:
    """403 unless the org may spend AI credits (trial, paid, or past-due grace)."""
    entitlement = await get_entitlement(org_id)
    if entitlement.blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=entitlement.message)
    return entitlement


async def ensure_invite_access(org_id: str) -> Entitlement:
    """403 for read-only (past due after grace) and unverified-plan orgs."""
    entitlement = await get_entitlement(org_id)
    if entitlement.read_only or entitlement.state == ACCESS_UNVERIFIED_PLAN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=entitlement.message)
    return entitlement


#: Recorded status for background AI work skipped by the entitlement gate.
SKIPPED_NO_ENTITLEMENT = "skipped"


async def background_ai_entitlement(org_id: Optional[str], database, task: str) -> Optional[Entitlement]:
    """Worker-side gate: returns the entitlement, or logs and returns None (skip, don't raise)."""
    if not org_id:
        logger.warning("Skipping %s: no organization id", task)
        return None
    entitlement = await get_entitlement(org_id, database)
    if entitlement.blocked:
        logger.info("Skipping %s for org %s: no AI entitlement (state=%s)", task, org_id, entitlement.state)
        return None
    return entitlement


async def release_trial_upload(org_id: str) -> None:
    """Refund one trial upload slot. Used when an upload fails after reservation.

    Safe to call multiple times; will never take the counter below zero.
    Paid and read-only orgs never consumed a slot, so a failed upload must
    not decrement their counter.
    """
    raw = await db.organization_billing.find_one({"organizationId": org_id}) or {}
    if upload_gate(raw) != "trial":
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


@router.get("/subscription", response_model=SubscriptionInfo)
async def get_subscription(current_user: dict = Depends(get_current_user)):
    """Get current organization's subscription status"""
    org_id = current_user["organizationId"]
    billing = await get_or_create_billing(org_id)
    
    # Check if trial expired
    is_trial_expired = False
    if billing.trialEndDate:
        trial_end = datetime.fromisoformat(billing.trialEndDate.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > trial_end:
            is_trial_expired = True
    
    raw = await db.organization_billing.find_one({"organizationId": org_id}) or {}
    entitlement = compute_entitlement(raw)
    return SubscriptionInfo(
        subscriptionTier=billing.subscriptionTier,
        billingStatus=billing.billingStatus,
        trialEndDate=billing.trialEndDate if not is_trial_expired else None,
        currentPeriodEnd=billing.currentPeriodEnd,
        cancelAtPeriodEnd=billing.cancelAtPeriodEnd,
        trialContractsUsed=billing.trialContractsUsed,
        trialContractsLimit=billing.trialContractsLimit,
        stripeCustomerId=billing.stripeCustomerId,
        stripeSubscriptionId=billing.stripeSubscriptionId,
        readOnly=upload_gate(raw) == "readonly",
        graceEndsAt=grace_ends_at(raw),
        accessState=entitlement.state,
        planId=entitlement.plan_id,
        maxMembers=entitlement.max_members,
        accessMessage=entitlement.message,
        billingUiEnabled=billing_ui_enabled(),
    )


@router.get("/capabilities")
async def get_capabilities(current_user: dict = Depends(get_current_user)):
    """Authoritative organization access and role capability state for the SPA."""
    entitlement = await get_entitlement(current_user["organizationId"])
    read_only = entitlement.state in (ACCESS_PAST_DUE, ACCESS_CANCELLED, ACCESS_UNVERIFIED_PLAN, "trial_expired")
    return {
        "state": entitlement.state,
        "readOnly": read_only,
        "message": entitlement.message,
        "capabilities": capability_map(current_user.get("role"), read_only=read_only),
    }


@router.get("/config")
async def get_billing_config():
    """Runtime billing flags for the frontend (no auth; nothing sensitive)."""
    return {"billingUiEnabled": billing_ui_enabled()}


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
    
    price_id = resolve_checkout_price(request)

    org_id = current_user["organizationId"]
    billing = await get_or_create_billing(org_id)

    # One subscription per org: a second checkout would bill the customer twice.
    if has_live_subscription(billing):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_ALREADY_SUBSCRIBED)
    # Webhooks can lag behind a just-completed checkout, so ask Stripe as well.
    if billing.stripeCustomerId:
        try:
            live = _stripe_has_live_subscription(billing.stripeCustomerId)
        except stripe.StripeError as e:
            logger.error("Could not list subscriptions for org %s: %s", org_id, type(e).__name__)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="Billing is temporarily unavailable. Please try again.")
        if live:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_ALREADY_SUBSCRIBED)

    # Create or get Stripe customer
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
    
    try:
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
            subscription_data=checkout_subscription_data(billing, org_id),
            metadata={"organizationId": org_id}
        )
        return CheckoutSessionResponse(sessionId=session.id, url=session.url)
    except stripe.StripeError as e:
        logger.error(f"Stripe checkout error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
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
        logger.error(f"Stripe portal error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
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
    
    logger.info(f"Received Stripe webhook: {event_type}")
    
    if event_type == "checkout.session.completed":
        await handle_checkout_completed(data)
    elif event_type == "customer.subscription.created":
        await handle_subscription_created(data)
    elif event_type == "customer.subscription.updated":
        await handle_subscription_updated(data)
    elif event_type == "customer.subscription.deleted":
        await handle_subscription_deleted(data)
    elif event_type == "invoice.payment_failed":
        await handle_payment_failed(data)
    elif event_type == "invoice.paid":
        await handle_invoice_paid(data)
    
    return {"received": True}


async def handle_checkout_completed(session):
    """Handle successful checkout session"""
    org_id = session.get("metadata", {}).get("organizationId")
    if not org_id:
        return
    
    subscription_id = session.get("subscription")
    customer_id = session.get("customer")
    
    if subscription_id:
        subscription = stripe.Subscription.retrieve(subscription_id)
        await update_billing_from_subscription(org_id, subscription, customer_id)


async def handle_subscription_created(subscription):
    """Handle subscription creation"""
    org_id = subscription.get("metadata", {}).get("organizationId")
    if not org_id:
        return
    customer_id = subscription.get("customer")
    await update_billing_from_subscription(org_id, subscription, customer_id)


async def handle_subscription_updated(subscription):
    """Handle subscription updates"""
    org_id = subscription.get("metadata", {}).get("organizationId")
    if not org_id:
        # Try to find by customer
        customer_id = subscription.get("customer")
        if customer_id:
            billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
            if billing:
                org_id = billing["organizationId"]
    if not org_id:
        return
    customer_id = subscription.get("customer")
    await update_billing_from_subscription(org_id, subscription, customer_id)


async def handle_subscription_deleted(subscription):
    """Handle subscription cancellation"""
    org_id = subscription.get("metadata", {}).get("organizationId")
    if not org_id:
        customer_id = subscription.get("customer")
        if customer_id:
            billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
            if billing:
                org_id = billing["organizationId"]
    if not org_id:
        return
    current = (await db.organization_billing.find_one({"organizationId": org_id}) or {}).get("stripeSubscriptionId")
    if _is_foreign_subscription(current, subscription.get("id")):
        # Deleting a duplicate or abandoned subscription must not cancel the org.
        logger.warning("Ignoring deletion of subscription %s for org %s (current %s)",
                       subscription.get("id"), org_id, current)
        return

    await db.organization_billing.update_one(
        {"organizationId": org_id},
        {
            "$set": {
                "subscriptionTier": SubscriptionTier.CANCELLED,
                "billingStatus": BillingStatus.CANCELLED,
                "cancelledAt": datetime.now(timezone.utc).isoformat(),
                "stripeSubscriptionId": None,
                "updatedAt": datetime.now(timezone.utc).isoformat()
            },
            "$unset": {"pastDueSince": ""},
        }
    )


def _invoice_subscription_id(invoice) -> Optional[str]:
    """Subscription an invoice belongs to (pre- and post-2025 Stripe API shapes)."""
    sub = invoice.get("subscription")
    if not sub:
        parent = invoice.get("parent") or {}
        sub = (parent.get("subscription_details") or {}).get("subscription")
    if sub is not None and not isinstance(sub, str):
        sub = sub.get("id")
    return sub or None


def _is_foreign_subscription(current: Optional[str], subscription_id: Optional[str]) -> bool:
    return bool(subscription_id and current and subscription_id != current)


async def handle_payment_failed(invoice):
    """Handle failed payment"""
    customer_id = invoice.get("customer")
    billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
    if not billing:
        return
    if _is_foreign_subscription(billing.get("stripeSubscriptionId"), _invoice_subscription_id(invoice)):
        logger.warning("Ignoring payment failure for a non-current subscription of org %s", billing["organizationId"])
        return
    
    now = datetime.now(timezone.utc).isoformat()
    await db.organization_billing.update_one(
        {"organizationId": billing["organizationId"]},
        {"$set": {
            "billingStatus": BillingStatus.PAST_DUE,
            "subscriptionTier": SubscriptionTier.TEAM,
            "pastDueSince": billing.get("pastDueSince") or now,
            "updatedAt": now,
        }}
    )


async def handle_invoice_paid(invoice):
    """Handle successful invoice payment"""
    customer_id = invoice.get("customer")
    billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
    if not billing:
        return
    if _is_foreign_subscription(billing.get("stripeSubscriptionId"), _invoice_subscription_id(invoice)):
        return

    # If was past_due, restore to active
    if billing.get("billingStatus") == BillingStatus.PAST_DUE:
        await db.organization_billing.update_one(
            {"organizationId": billing["organizationId"]},
            {
                "$set": {"billingStatus": BillingStatus.ACTIVE, "updatedAt": datetime.now(timezone.utc).isoformat()},
                "$unset": {"pastDueSince": ""},
            }
        )


async def update_billing_from_subscription(org_id: str, subscription, customer_id: str):
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
    
    # A failed payment stays on the team plan during the grace window. Only a
    # cancelled subscription (or an unfinished checkout) leaves the paid tier.
    if billing_status in (BillingStatus.ACTIVE, BillingStatus.TRIALING, BillingStatus.PAST_DUE):
        tier = SubscriptionTier.TEAM
    elif billing_status == BillingStatus.CANCELLED:
        tier = SubscriptionTier.CANCELLED
    else:
        tier = SubscriptionTier.TRIAL
    
    current_period_end = None
    if subscription.get("current_period_end"):
        current_period_end = datetime.fromtimestamp(subscription["current_period_end"], tz=timezone.utc).isoformat()
    
    trial_end = None
    if subscription.get("trial_end"):
        trial_end = datetime.fromtimestamp(subscription["trial_end"], tz=timezone.utc).isoformat()
    
    existing = await db.organization_billing.find_one({"organizationId": org_id}) or {}
    current = existing.get("stripeSubscriptionId")
    if (_is_foreign_subscription(current, subscription.get("id"))
            and existing.get("billingStatus") in LIVE_BILLING_STATUSES
            and billing_status not in LIVE_BILLING_STATUSES):
        # A dead duplicate must not overwrite the org's live subscription.
        logger.warning("Ignoring %s subscription %s for org %s (current %s is live)",
                       stripe_status, subscription.get("id"), org_id, current)
        return

    price_id = subscription.get("items", {}).get("data", [{}])[0].get("price", {}).get("id")
    if tier == SubscriptionTier.TEAM and plan_config.plan_for_price_id(price_id) is None:
        # B4: recorded, but compute_entitlement grants no access (unverified_plan).
        logger.error("Subscription %s for org %s uses unconfigured Stripe price %s",
                     subscription.get("id"), org_id, price_id)

    now = datetime.now(timezone.utc).isoformat()
    fields = {
        "stripeCustomerId": customer_id,
        "stripeSubscriptionId": subscription.get("id"),
        "stripePriceId": price_id,
        "subscriptionTier": tier,
        "billingStatus": billing_status,
        "currentPeriodStart": datetime.fromtimestamp(subscription.get("current_period_start", 0), tz=timezone.utc).isoformat() if subscription.get("current_period_start") else None,
        "currentPeriodEnd": current_period_end,
        # Never erase the in-app trial end when Stripe has no trial.
        "trialEndDate": trial_end or existing.get("trialEndDate"),
        "cancelAtPeriodEnd": subscription.get("cancel_at_period_end", False),
        "updatedAt": now,
    }
    update = {"$set": fields}
    if billing_status == BillingStatus.PAST_DUE:
        fields["pastDueSince"] = existing.get("pastDueSince") or now
    else:
        update["$unset"] = {"pastDueSince": ""}
    await db.organization_billing.update_one({"organizationId": org_id}, update)


def _plan_card(plan: plan_config.PlanConfig) -> dict:
    monthly = display_price(plan, "monthly")
    annual = display_price(plan, "annual")
    seats = f"Up to {plan.max_members} team members" if plan.max_members else "Unlimited team members"
    return {
        "id": plan.id,
        "name": f"{plan.name} plan",
        # Same Stripe price id checkout charges for (planId, "monthly").
        "price": monthly["amount"],
        "interval": "month",
        "priceId": monthly["priceId"],
        "priceSource": monthly["source"],
        "currency": monthly["currency"],
        "taxBehavior": monthly["taxBehavior"],
        "taxInclusive": monthly["taxInclusive"],
        "priceLabel": monthly["priceLabel"],
        "annual": {"price": annual["amount"], "interval": "year", "priceId": annual["priceId"],
                   "priceSource": annual["source"], "taxInclusive": annual["taxInclusive"],
                   "priceLabel": annual["priceLabel"]},
        "maxMembers": plan.max_members,
        "features": [
            "AI contract analysis",
            "Contract repository",
            seats,
            "Role-based access control",
            "Contract templates library",
            "PDF export",
        ],
        "cta": f"Upgrade to {plan.name}",
        "available": bool(monthly["priceId"] or annual["priceId"]),
    }


@router.get("/plans")
async def get_plans():
    """Available plans. Prices come from the same Stripe price IDs checkout uses."""
    trial_seats = plan_config.trial_max_members()
    return {
        "plans": [
            {
                "id": "trial",
                "name": "Free trial",
                "price": 0,
                "interval": "month",
                "maxMembers": trial_seats,
                "features": [
                    "Free trial",
                    "Limited contract uploads",
                    f"Up to {trial_seats} team members" if trial_seats else "Unlimited team members",
                    "AI contract analysis",
                    "Contract repository",
                    "Basic dashboard"
                ],
                "cta": "Start free trial"
            },
            *[_plan_card(p) for p in plan_config.get_paid_plans().values()],
        ]
    }


# Export the check function for use in contracts route
__all__ = [
    "router",
    "check_trial_access",
    "reserve_trial_upload_atomic",
    "release_trial_upload",
    "assert_org_writable",
    "increment_trial_usage",
    "get_or_create_billing",
    "init_db",
    "member_limit_for_org",
    "get_entitlement",
    "ensure_ai_access",
    "ensure_invite_access",
    "background_ai_entitlement",
    "SKIPPED_NO_ENTITLEMENT",
    "compute_entitlement",
]
