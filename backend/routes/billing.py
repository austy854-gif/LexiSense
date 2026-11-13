from fastapi import APIRouter, HTTPException, status, Depends, Request
from typing import Optional
from datetime import datetime, timezone, timedelta
import logging
import uuid
from dataclasses import dataclass
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
    WebhookEvent,
)
from utils.auth import get_current_user, require_role
from utils.billing_access import (
    ACCESS_CANCELLED,
    ACCESS_GRACE,
    ACCESS_PAID,
    ACCESS_TRIAL_EXPIRED,
    ACCESS_UNVERIFIED_PLAN,
    FEATURE_AI,
    FEATURE_INVITE,
    FEATURE_UPLOAD,
    FEATURE_WRITE,
    Entitlement,
    compute_entitlement,
    entitlement_allows,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=CHECKOUT_PRICE_REJECTED
        )
    if request.planId:
        plan = plan_config.get_paid_plan(request.planId)
        price_id = plan.price_for_interval(request.interval) if plan else None
    else:
        price_id = (request.priceId or "").strip()
    if not price_id or price_id not in allowed:
        logger.warning(
            "Checkout rejected: price not in allowlist",
            extra={
                "plan_id": request.planId,
                "interval": request.interval,
                "price_id": request.priceId,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=CHECKOUT_PRICE_REJECTED
        )
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


def checkout_subscription_data(
    billing: OrganizationBilling, org_id: str, now: Optional[datetime] = None
) -> dict:
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
LIVE_BILLING_STATUSES = frozenset(
    {BillingStatus.ACTIVE, BillingStatus.TRIALING, BillingStatus.PAST_DUE}
)
LIVE_STRIPE_STATUSES = frozenset({"active", "trialing", "past_due", "unpaid"})
MSG_ALREADY_SUBSCRIBED = "Your organization already has a subscription. Use the billing portal to manage or change it."


def has_live_subscription(billing: OrganizationBilling) -> bool:
    return (
        billing.subscriptionTier == SubscriptionTier.TEAM
        and billing.billingStatus in LIVE_BILLING_STATUSES
    )


def _stripe_has_live_subscription(customer_id: str) -> bool:
    subs = stripe.Subscription.list(customer=customer_id, status="all", limit=20)
    return any(
        sub.get("status") in LIVE_STRIPE_STATUSES for sub in (subs.get("data") or [])
    )


async def member_limit_for_org(org_id: str) -> Optional[int]:
    """Seat cap for the org's current tier, from utils.plans.

    Live paid subscriptions get their plan's cap (looked up from the stored
    Stripe price). Everything else -- trial, cancelled, or a price that isn't
    configured -- gets the trial cap (fail closed to the smallest tier).
    """
    billing = await db.organization_billing.find_one({"organizationId": org_id}) or {}
    entitlement = compute_entitlement(billing)
    if entitlement.state == ACCESS_UNVERIFIED_PLAN:
        logger.error(
            "Org %s has a subscription on an unknown Stripe price; applying trial seat cap",
            org_id,
        )
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
        trialContractsLimit=TRIAL_CONTRACT_LIMIT,
    )
    try:
        await database.organization_billing.insert_one(new_billing.model_dump())
    except DuplicateKeyError:
        existing = await database.organization_billing.find_one(
            {"organizationId": org_id}
        )
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
    if not entitlement_allows(entitlement.state, FEATURE_UPLOAD):
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
    billing_doc = (
        await db.organization_billing.find_one({"organizationId": org_id}) or {}
    )
    entitlement = compute_entitlement(billing_doc)
    if entitlement.state in (ACCESS_PAID, ACCESS_GRACE):
        return True, ""
    if not entitlement_allows(entitlement.state, FEATURE_UPLOAD):
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
    if not entitlement_allows(entitlement.state, FEATURE_WRITE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=entitlement.message
        )


def trial_limit_message() -> str:
    step = (
        "Upgrade to keep adding contracts."
        if billing_ui_enabled()
        else "Please contact support to continue."
    )
    return f"You've used all your trial uploads. {step}"


async def get_entitlement(org_id: str, database=None) -> Entitlement:
    database = db if database is None else database
    await get_or_create_billing(org_id, database)
    raw = await database.organization_billing.find_one({"organizationId": org_id}) or {}
    return compute_entitlement(raw)


async def ensure_ai_access(org_id: str) -> Entitlement:
    """403 unless the org may spend AI credits (trial, paid, or past-due grace)."""
    entitlement = await get_entitlement(org_id)
    if not entitlement_allows(entitlement.state, FEATURE_AI):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=entitlement.message
        )
    return entitlement


#: Shown when a cancelled org tries to invite a teammate.
MSG_CANCELLED_INVITE = (
    "Your plan is cancelled. Reactivate your subscription to invite teammates."
)
#: Billing UI off: the billing page is hidden, so point to support instead.
MSG_CANCELLED_INVITE_SUPPORT = (
    "Your plan is cancelled. Please contact support to invite teammates."
)


def cancelled_invite_message() -> str:
    return (
        MSG_CANCELLED_INVITE if billing_ui_enabled() else MSG_CANCELLED_INVITE_SUPPORT
    )


async def ensure_invite_access(org_id: str) -> Entitlement:
    """403 for cancelled, read-only (past due after grace) and unverified-plan orgs."""
    entitlement = await get_entitlement(org_id)
    if entitlement.state == ACCESS_CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=cancelled_invite_message()
        )
    if not entitlement_allows(entitlement.state, FEATURE_INVITE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=entitlement.message
        )
    return entitlement


#: Recorded status for background AI work skipped by the entitlement gate.
SKIPPED_NO_ENTITLEMENT = "skipped"


async def background_ai_entitlement(
    org_id: Optional[str], database, task: str
) -> Optional[Entitlement]:
    """Worker-side gate: returns the entitlement, or logs and returns None (skip, don't raise)."""
    if not org_id:
        logger.warning("Skipping %s: no organization id", task)
        return None
    entitlement = await get_entitlement(org_id, database)
    if entitlement.blocked:
        logger.info(
            "Skipping %s for org %s: no AI entitlement (state=%s)",
            task,
            org_id,
            entitlement.state,
        )
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
        {
            "$inc": {"trialContractsUsed": 1},
            "$set": {"updatedAt": datetime.now(timezone.utc).isoformat()},
        },
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
    read_only = not entitlement_allows(entitlement.state, FEATURE_WRITE)
    can_upload, upload_message = await check_trial_access(
        current_user["organizationId"]
    )
    return {
        "state": entitlement.state,
        "readOnly": read_only,
        "message": entitlement.message or upload_message,
        # The entitlement grid row for this org (utils.billing_access).
        "features": {
            feature: entitlement_allows(entitlement.state, feature)
            for feature in (FEATURE_UPLOAD, FEATURE_AI, FEATURE_WRITE, FEATURE_INVITE)
        },
        "capabilities": capability_map(
            current_user.get("role"),
            read_only=read_only,
            uploads_blocked=entitlement.state == ACCESS_TRIAL_EXPIRED or not can_upload,
        ),
    }


@router.get("/config")
async def get_billing_config():
    """Runtime billing flags for the frontend (no auth; nothing sensitive)."""
    return {"billingUiEnabled": billing_ui_enabled()}


@router.post("/checkout", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    request: CheckoutSessionRequest, current_user: dict = Depends(require_role("admin"))
):
    """Create Stripe Checkout session for subscription"""
    if not stripe_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured. Please contact support.",
        )

    price_id = resolve_checkout_price(request)

    org_id = current_user["organizationId"]
    billing = await get_or_create_billing(org_id)

    # One subscription per org: a second checkout would bill the customer twice.
    if has_live_subscription(billing):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=MSG_ALREADY_SUBSCRIBED
        )
    # Webhooks can lag behind a just-completed checkout, so ask Stripe as well.
    if billing.stripeCustomerId:
        try:
            live = _stripe_has_live_subscription(billing.stripeCustomerId)
        except stripe.StripeError as e:
            logger.error(
                "Could not list subscriptions for org %s: %s", org_id, type(e).__name__
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Billing is temporarily unavailable. Please try again.",
            )
        if live:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=MSG_ALREADY_SUBSCRIBED
            )

    # Create or get Stripe customer
    if not billing.stripeCustomerId:
        customer = stripe.Customer.create(
            email=current_user["email"], metadata={"organizationId": org_id}
        )
        billing.stripeCustomerId = customer.id
        await db.organization_billing.update_one(
            {"organizationId": org_id},
            {
                "$set": {
                    "stripeCustomerId": customer.id,
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    try:
        session = stripe.checkout.Session.create(
            customer=billing.stripeCustomerId,
            payment_method_types=["card"],
            line_items=[
                {
                    "price": price_id,
                    "quantity": 1,
                }
            ],
            mode="subscription",
            success_url=request.successUrl,
            cancel_url=request.cancelUrl,
            subscription_data=checkout_subscription_data(billing, org_id),
            metadata={"organizationId": org_id},
        )
        return CheckoutSessionResponse(sessionId=session.id, url=session.url)
    except stripe.StripeError as e:
        logger.error(f"Stripe checkout error: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/portal", response_model=BillingPortalResponse)
async def create_billing_portal(
    request: BillingPortalRequest, current_user: dict = Depends(require_role("admin"))
):
    """Create Stripe Billing Portal session for subscription management"""
    if not stripe_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured. Please contact support.",
        )

    org_id = current_user["organizationId"]
    billing = await get_or_create_billing(org_id)

    if not billing.stripeCustomerId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing account found. Please subscribe first.",
        )

    try:
        session = stripe.billing_portal.Session.create(
            customer=billing.stripeCustomerId,
            return_url=request.returnUrl,
        )
        return BillingPortalResponse(url=session.url)
    except stripe.StripeError as e:
        logger.error(f"Stripe portal error: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


#: Unique index that makes each Stripe event id claimable exactly once.
STRIPE_EVENTS_INDEX = "uq_stripe_events_event_id"
#: A "processing" claim older than this has no live worker and may be reclaimed.
STRIPE_EVENT_LEASE = timedelta(minutes=10)
_stripe_event_index_ready: set = set()

EVENT_CLAIMED = "claimed"
EVENT_DONE = "done"
EVENT_BUSY = "busy"


async def ensure_stripe_event_index(database) -> None:
    """Create (or confirm) the unique Stripe event-id index.

    Raises if the index cannot be confirmed; the webhook must not process
    events without it. Success is cached per database handle, so a failure is
    retried on the next delivery.
    """
    if id(database) in _stripe_event_index_ready:
        return
    await database.stripe_events.create_index(
        "eventId", unique=True, name=STRIPE_EVENTS_INDEX
    )
    _stripe_event_index_ready.add(id(database))


async def _claim_stripe_event(
    event_id: str, event_type: str, created: Optional[int]
) -> tuple[str, Optional[str]]:
    """Atomically claim a Stripe event for processing.

    Returns ``(EVENT_CLAIMED, token)`` when this delivery owns the event,
    ``(EVENT_DONE, None)`` when it was already processed, and
    ``(EVENT_BUSY, None)`` when another delivery holds a live claim. A failed
    event, or a ``processing`` claim whose lease expired (the worker crashed),
    can be reclaimed.
    """
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex
    claim = {
        "status": "processing",
        "claimToken": token,
        "claimedAt": now.isoformat(),
    }
    try:
        await db.stripe_events.insert_one(
            {
                "eventId": event_id,
                "type": event_type,
                "created": created,
                "receivedAt": now.isoformat(),
                "attempts": 1,
                **claim,
            }
        )
        return EVENT_CLAIMED, token
    except DuplicateKeyError:
        pass

    stale_before = (now - STRIPE_EVENT_LEASE).isoformat()
    reclaimed = await db.stripe_events.update_one(
        {
            "eventId": event_id,
            "$or": [
                {"status": "failed"},
                {"status": "processing", "claimedAt": {"$lte": stale_before}},
            ],
        },
        {"$set": claim, "$inc": {"attempts": 1}},
    )
    if reclaimed.modified_count:
        return EVENT_CLAIMED, token
    doc = await db.stripe_events.find_one(
        {"eventId": event_id}, {"_id": 0, "status": 1}
    )
    if doc and doc.get("status") == EVENT_DONE:
        return EVENT_DONE, None
    return EVENT_BUSY, None


async def _settle_stripe_event(event_id: str, token: str, fields: dict) -> bool:
    """Record the outcome of a claimed event; only the claim holder may write.

    Returns False if the outcome could not be recorded. The claim then expires
    and the next delivery reprocesses the event, which is safe because each
    billing write refuses an event already applied to the record.
    """
    try:
        result = await db.stripe_events.update_one(
            {"eventId": event_id, "claimToken": token},
            {"$set": {**fields, "settledAt": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception:
        logger.exception("Could not record outcome of Stripe event %s", event_id)
        return False
    if not result.matched_count:
        logger.error("Lost the claim on Stripe event %s before settling", event_id)
        return False
    return True


def _event_order_filter(org_filter: dict, event_created: Optional[int]) -> dict:
    """Restrict a billing write to records no newer than this event.

    ``lastStripeEventCreated`` holds the ``created`` time of the newest event
    applied to the org, so an older event delivered late cannot overwrite newer
    subscription state. Without an event time the write is unconditional (the
    webhook rejects events without one; only direct calls omit it).

    Residual window: Stripe's ``created`` has one-second resolution and events
    carry no finer monotonic sequence, so two distinct events created in the
    same second are applied in arrival order. A strict ``<`` would instead drop
    legitimate same-second events, so ``<=`` is the accepted trade-off.
    """
    if event_created is None:
        return org_filter
    return {
        **org_filter,
        "$or": [
            {"lastStripeEventCreated": None},
            {"lastStripeEventCreated": {"$lte": event_created}},
        ],
    }


@dataclass(frozen=True)
class StripeEventRef:
    """The identity and creation time of the webhook event being applied."""

    id: str
    created: int


#: Recently applied event ids kept on each billing record (replay guard).
APPLIED_EVENT_IDS_KEPT = 50


async def _apply_billing_event(
    org_id: str, update: dict, ref: Optional[StripeEventRef], what: str
) -> bool:
    """Apply ``update`` to the org's billing record unless a newer event won or
    this event was already applied.

    The applied-event check makes a reapplication (a redelivery after an
    unconfirmed settlement) a no-op in the same atomic write, so the billing
    record never sees one event twice.
    """
    if ref is None:
        await db.organization_billing.update_one({"organizationId": org_id}, update)
        return True
    update.setdefault("$set", {})["lastStripeEventCreated"] = ref.created
    update["$push"] = {
        "appliedStripeEventIds": {
            "$each": [ref.id],
            "$slice": -APPLIED_EVENT_IDS_KEPT,
        }
    }
    flt = _event_order_filter({"organizationId": org_id}, ref.created)
    flt["appliedStripeEventIds"] = {"$ne": ref.id}
    result = await db.organization_billing.update_one(flt, update)
    if not result.matched_count:
        logger.warning(
            "Ignoring Stripe %s for org %s (event %s, created %s): already "
            "applied, a newer event was applied, or there is no billing record",
            what,
            org_id,
            ref.id,
            ref.created,
        )
        return False
    return True


async def _dispatch_stripe_event(event_type: str, data, ref: "StripeEventRef") -> None:
    if event_type == "checkout.session.completed":
        await handle_checkout_completed(data, ref)
    elif event_type == "customer.subscription.created":
        await handle_subscription_created(data, ref)
    elif event_type == "customer.subscription.updated":
        await handle_subscription_updated(data, ref)
    elif event_type == "customer.subscription.deleted":
        await handle_subscription_deleted(data, ref)
    elif event_type == "invoice.payment_failed":
        await handle_payment_failed(data, ref)
    elif event_type == "invoice.paid":
        await handle_invoice_paid(data, ref)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events exactly once, in order.

    Each event id is claimed atomically in ``stripe_events``: a redelivery of a
    processed event returns 200 without reprocessing, a concurrent duplicate
    gets 409 (Stripe retries it), and a failed or crashed attempt is retried
    by Stripe's next delivery.
    """
    if not stripe_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing not configured",
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

    try:
        event_id = event["id"]
        event_type = event["type"]
        data = event["data"]["object"]
    except (KeyError, TypeError):
        logger.error("Stripe webhook payload is missing id, type or data")
        raise HTTPException(status_code=400, detail="Invalid payload")
    if not event_id:
        raise HTTPException(status_code=400, detail="Invalid payload")
    event_created = event.get("created")
    if isinstance(event_created, bool) or not isinstance(event_created, int):
        # Ordering depends on an authoritative event time; never apply without it.
        logger.error("Stripe event %s has no valid created time", event_id)
        raise HTTPException(status_code=400, detail="Invalid payload")

    logger.info(f"Received Stripe webhook: {event_type} ({event_id})")

    try:
        await ensure_stripe_event_index(db)
        outcome, token = await _claim_stripe_event(event_id, event_type, event_created)
    except Exception:
        logger.exception("Stripe event ledger unavailable; not processing %s", event_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook temporarily unavailable",
        )
    if outcome == EVENT_DONE:
        return {"received": True, "duplicate": True}
    if outcome == EVENT_BUSY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Event is already being processed",
        )

    try:
        await _dispatch_stripe_event(
            event_type, data, StripeEventRef(id=event_id, created=event_created)
        )
    except Exception as exc:
        logger.exception("Stripe webhook %s (%s) failed", event_type, event_id)
        await _settle_stripe_event(
            event_id, token, {"status": "failed", "error": type(exc).__name__}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed",
        )
    if not await _settle_stripe_event(
        event_id, token, {"status": EVENT_DONE, "error": None}
    ):
        # Not acknowledged: Stripe redelivers, and the replay guard makes the
        # reprocessing a no-op for billing writes already applied.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed",
        )
    return {"received": True}


async def handle_checkout_completed(session, ref: Optional["StripeEventRef"] = None):
    """Handle successful checkout session"""
    org_id = session.get("metadata", {}).get("organizationId")
    if not org_id:
        return

    subscription_id = session.get("subscription")
    customer_id = session.get("customer")

    if subscription_id:
        subscription = stripe.Subscription.retrieve(subscription_id)
        await update_billing_from_subscription(org_id, subscription, customer_id, ref)


async def handle_subscription_created(
    subscription, ref: Optional["StripeEventRef"] = None
):
    """Handle subscription creation"""
    org_id = subscription.get("metadata", {}).get("organizationId")
    if not org_id:
        return
    customer_id = subscription.get("customer")
    await update_billing_from_subscription(org_id, subscription, customer_id, ref)


async def handle_subscription_updated(
    subscription, ref: Optional["StripeEventRef"] = None
):
    """Handle subscription updates"""
    org_id = subscription.get("metadata", {}).get("organizationId")
    if not org_id:
        # Try to find by customer
        customer_id = subscription.get("customer")
        if customer_id:
            billing = await db.organization_billing.find_one(
                {"stripeCustomerId": customer_id}
            )
            if billing:
                org_id = billing["organizationId"]
    if not org_id:
        return
    customer_id = subscription.get("customer")
    await update_billing_from_subscription(org_id, subscription, customer_id, ref)


async def handle_subscription_deleted(
    subscription, ref: Optional["StripeEventRef"] = None
):
    """Handle subscription cancellation"""
    org_id = subscription.get("metadata", {}).get("organizationId")
    if not org_id:
        customer_id = subscription.get("customer")
        if customer_id:
            billing = await db.organization_billing.find_one(
                {"stripeCustomerId": customer_id}
            )
            if billing:
                org_id = billing["organizationId"]
    if not org_id:
        return
    current = (
        await db.organization_billing.find_one({"organizationId": org_id}) or {}
    ).get("stripeSubscriptionId")
    if _is_foreign_subscription(current, subscription.get("id")):
        # Deleting a duplicate or abandoned subscription must not cancel the org.
        logger.warning(
            "Ignoring deletion of subscription %s for org %s (current %s)",
            subscription.get("id"),
            org_id,
            current,
        )
        return

    await _apply_billing_event(
        org_id,
        {
            "$set": {
                "subscriptionTier": SubscriptionTier.CANCELLED,
                "billingStatus": BillingStatus.CANCELLED,
                "cancelledAt": datetime.now(timezone.utc).isoformat(),
                "stripeSubscriptionId": None,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            },
            "$unset": {"pastDueSince": ""},
        },
        ref,
        "subscription deletion",
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


def _is_foreign_subscription(
    current: Optional[str], subscription_id: Optional[str]
) -> bool:
    return bool(subscription_id and current and subscription_id != current)


async def handle_payment_failed(invoice, ref: Optional["StripeEventRef"] = None):
    """Handle failed payment"""
    customer_id = invoice.get("customer")
    billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
    if not billing:
        return
    if _is_foreign_subscription(
        billing.get("stripeSubscriptionId"), _invoice_subscription_id(invoice)
    ):
        logger.warning(
            "Ignoring payment failure for a non-current subscription of org %s",
            billing["organizationId"],
        )
        return

    now = datetime.now(timezone.utc).isoformat()
    await _apply_billing_event(
        billing["organizationId"],
        {
            "$set": {
                "billingStatus": BillingStatus.PAST_DUE,
                "subscriptionTier": SubscriptionTier.TEAM,
                "pastDueSince": billing.get("pastDueSince") or now,
                "updatedAt": now,
            }
        },
        ref,
        "payment failure",
    )


async def handle_invoice_paid(invoice, ref: Optional["StripeEventRef"] = None):
    """Handle successful invoice payment"""
    customer_id = invoice.get("customer")
    billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
    if not billing:
        return
    if _is_foreign_subscription(
        billing.get("stripeSubscriptionId"), _invoice_subscription_id(invoice)
    ):
        return

    if billing.get("billingStatus") != BillingStatus.PAST_DUE:
        # Nothing to restore, but record the event so an older payment failure
        # delivered later cannot move the paid account to past due.
        if ref is not None:
            await _apply_billing_event(
                billing["organizationId"], {"$set": {}}, ref, "invoice payment"
            )
        return
    # Was past due: restore to active.
    await _apply_billing_event(
        billing["organizationId"],
        {
            "$set": {
                "billingStatus": BillingStatus.ACTIVE,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            },
            "$unset": {"pastDueSince": ""},
        },
        ref,
        "invoice payment",
    )


async def update_billing_from_subscription(
    org_id: str,
    subscription,
    customer_id: str,
    ref: Optional["StripeEventRef"] = None,
):
    """Update billing record from Stripe subscription object.

    With ``ref`` the write is skipped if a newer event, or this same event, was
    already applied.
    """
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
    if billing_status in (
        BillingStatus.ACTIVE,
        BillingStatus.TRIALING,
        BillingStatus.PAST_DUE,
    ):
        tier = SubscriptionTier.TEAM
    elif billing_status == BillingStatus.CANCELLED:
        tier = SubscriptionTier.CANCELLED
    else:
        tier = SubscriptionTier.TRIAL

    current_period_end = None
    if subscription.get("current_period_end"):
        current_period_end = datetime.fromtimestamp(
            subscription["current_period_end"], tz=timezone.utc
        ).isoformat()

    trial_end = None
    if subscription.get("trial_end"):
        trial_end = datetime.fromtimestamp(
            subscription["trial_end"], tz=timezone.utc
        ).isoformat()

    existing = await db.organization_billing.find_one({"organizationId": org_id}) or {}
    current = existing.get("stripeSubscriptionId")
    if (
        _is_foreign_subscription(current, subscription.get("id"))
        and existing.get("billingStatus") in LIVE_BILLING_STATUSES
        and billing_status not in LIVE_BILLING_STATUSES
    ):
        # A dead duplicate must not overwrite the org's live subscription.
        logger.warning(
            "Ignoring %s subscription %s for org %s (current %s is live)",
            stripe_status,
            subscription.get("id"),
            org_id,
            current,
        )
        return

    price_id = (
        subscription.get("items", {}).get("data", [{}])[0].get("price", {}).get("id")
    )
    if (
        tier == SubscriptionTier.TEAM
        and plan_config.plan_for_price_id(price_id) is None
    ):
        # B4: recorded, but compute_entitlement grants no access (unverified_plan).
        logger.error(
            "Subscription %s for org %s uses unconfigured Stripe price %s",
            subscription.get("id"),
            org_id,
            price_id,
        )

    now = datetime.now(timezone.utc).isoformat()
    fields = {
        "stripeCustomerId": customer_id,
        "stripeSubscriptionId": subscription.get("id"),
        "stripePriceId": price_id,
        "subscriptionTier": tier,
        "billingStatus": billing_status,
        "currentPeriodStart": (
            datetime.fromtimestamp(
                subscription.get("current_period_start", 0), tz=timezone.utc
            ).isoformat()
            if subscription.get("current_period_start")
            else None
        ),
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
    await _apply_billing_event(
        org_id, update, ref, f"subscription {stripe_status} update"
    )


def _plan_card(plan: plan_config.PlanConfig) -> dict:
    monthly = display_price(plan, "monthly")
    annual = display_price(plan, "annual")
    seats = (
        f"Up to {plan.max_members} team members"
        if plan.max_members
        else "Unlimited team members"
    )
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
        "annual": {
            "price": annual["amount"],
            "interval": "year",
            "priceId": annual["priceId"],
            "priceSource": annual["source"],
            "taxInclusive": annual["taxInclusive"],
            "priceLabel": annual["priceLabel"],
        },
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
                    (
                        f"Up to {trial_seats} team members"
                        if trial_seats
                        else "Unlimited team members"
                    ),
                    "AI contract analysis",
                    "Contract repository",
                    "Basic dashboard",
                ],
                "cta": "Start free trial",
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
