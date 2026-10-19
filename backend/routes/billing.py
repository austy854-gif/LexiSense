from fastapi import APIRouter, HTTPException, status, Depends, Request
from dataclasses import dataclass
from typing import Optional, Tuple, Union
from datetime import datetime, timezone, timedelta
import asyncio
import logging
import stripe
from pymongo.errors import DuplicateKeyError

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
from utils.feature_flags import billing_ui_enabled
from services.stripe_prices import reconcile_display_price
from utils.plans import (
    CHECKOUT_INTERVALS,
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

#: Statuses of a subscription that still exists and bills the customer. An org
#: in one of these may not start a second checkout, and webhooks for any other
#: subscription id must not overwrite it.
LIVE_BILLING_STATUSES = frozenset({BillingStatus.ACTIVE, BillingStatus.TRIALING, BillingStatus.PAST_DUE})
#: Same, as Stripe subscription statuses (``unpaid`` maps to past_due).
LIVE_STRIPE_STATUSES = frozenset({"active", "trialing", "past_due", "unpaid"})

MSG_ALREADY_SUBSCRIBED = (
    "Your organization already has a subscription. Use the billing portal to "
    "manage or change it."
)

ACCESS_PAID = "paid"
ACCESS_TRIAL = "trial"
ACCESS_TRIAL_EXPIRED = "trial_expired"
ACCESS_PAST_DUE = "past_due"
#: Payment failed but still inside BILLING_PAST_DUE_GRACE_DAYS: access continues.
ACCESS_PAST_DUE_GRACE = "past_due_grace"
ACCESS_CANCELLED = "cancelled"
ACCESS_INACTIVE = "inactive"
#: Team subscription whose Stripe price isn't in the plan config: no access.
ACCESS_UNVERIFIED_PLAN = "unverified_plan"

# User-facing messages. Each has a "billing UI on" form (points to the Billing
# page / Stripe portal) and a "billing UI off" form: while BILLING_UI_ENABLED is
# off the Billing page is hidden, so messages must not send users to it.
MSG_TRIAL_EXPIRED = "Your free trial has ended. Please upgrade to continue using LexiSense."
MSG_PAST_DUE = (
    "Your last payment failed, so your account is read-only. Please update your "
    "payment method in the billing portal to restore uploads, AI analysis, chat "
    "and invitations."
)
MSG_CANCELLED = "Your subscription has been cancelled. Please resubscribe to continue using LexiSense."
MSG_UNVERIFIED_PLAN = (
    "We couldn't verify your subscription plan. Please contact support so we can fix your account."
)
MSG_INACTIVE = "Your subscription is not active. Please complete checkout or contact support."

MSG_TRIAL_EXPIRED_NO_BILLING_UI = (
    "Your free trial has ended. Please contact support to continue using LexiSense."
)
MSG_PAST_DUE_NO_BILLING_UI = (
    "Your last payment failed, so your account is read-only. Please contact "
    "support to restore uploads, AI analysis, chat and invitations."
)
MSG_CANCELLED_NO_BILLING_UI = (
    "Your subscription has been cancelled. Please contact support to continue using LexiSense."
)
MSG_INACTIVE_NO_BILLING_UI = "Your subscription is not active. Please contact support."


def _billing_ui() -> bool:
    return billing_ui_enabled()


def _blocked_message(state: str) -> str:
    """Message for a blocked access state, matching whether the Billing UI is visible."""
    ui = _billing_ui()
    if state == ACCESS_TRIAL_EXPIRED:
        return MSG_TRIAL_EXPIRED if ui else MSG_TRIAL_EXPIRED_NO_BILLING_UI
    if state == ACCESS_PAST_DUE:
        return MSG_PAST_DUE if ui else MSG_PAST_DUE_NO_BILLING_UI
    if state == ACCESS_CANCELLED:
        return MSG_CANCELLED if ui else MSG_CANCELLED_NO_BILLING_UI
    if state == ACCESS_UNVERIFIED_PLAN:
        return MSG_UNVERIFIED_PLAN
    return MSG_INACTIVE if ui else MSG_INACTIVE_NO_BILLING_UI


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

    @property
    def read_only(self) -> bool:
        """Past-due beyond the grace period: existing contracts stay viewable,
        listable and downloadable; uploads, AI analysis, chat and new
        invitations are blocked with the payment-failed message."""
        return self.state == ACCESS_PAST_DUE


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
    if not _billing_ui():
        return (
            f"Your last payment failed. Please contact support within {days} {unit} "
            "to keep full access to LexiSense."
        )
    return (
        "Your last payment failed. Please update your payment method in the billing "
        f"portal within {days} {unit} to keep using LexiSense."
    )


def _resolve_paid_plan(billing) -> Optional[PlanConfig]:
    """The plan a subscription's Stripe price maps to, or ``None``.

    The stored Stripe price is authoritative and must be in the plan config's
    allow-list; an unknown / unmapped price grants NO paid plan (there is no
    fallback to the default plan's limits).
    """
    return plan_for_price_id(_billing_get(billing, "stripePriceId"))


def compute_entitlement(billing: Union[OrganizationBilling, dict, None], now: Optional[datetime] = None) -> Entitlement:
    """Single place that turns a billing record into entitlements.

    * Team tier with Stripe status ``active`` or ``trialing`` -> paid: unlimited
      uploads, AI access, the plan's member cap.
    * Team tier ``past_due`` -> still entitled (with a warning) for
      ``BILLING_PAST_DUE_GRACE_DAYS`` after ``pastDueSince``, then READ-ONLY
      (``ACCESS_PAST_DUE``): contracts can still be viewed, listed and
      downloaded; uploads, AI, chat and new invitations are blocked.
    * Team tier whose Stripe price isn't in the plan config -> no access.
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
        if plan is None:
            logger.error(
                "Org %s has a Team subscription with unmapped Stripe price %r; no paid access",
                _billing_get(billing, "organizationId"), _billing_get(billing, "stripePriceId"),
            )
            return Entitlement(
                ACCESS_UNVERIFIED_PLAN, None, False, False, 0, trial.max_members,
                _blocked_message(ACCESS_UNVERIFIED_PLAN),
            )
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
                ACCESS_PAST_DUE, plan.id, False, False, 0, plan.max_members, _blocked_message(ACCESS_PAST_DUE),
                grace_ends_at=grace_end,
            )
        if billing_status == BillingStatus.CANCELLED:
            return Entitlement(
                ACCESS_CANCELLED, plan.id, False, False, 0, plan.max_members, _blocked_message(ACCESS_CANCELLED)
            )
        return Entitlement(
            ACCESS_INACTIVE, plan.id, False, False, 0, plan.max_members, _blocked_message(ACCESS_INACTIVE)
        )

    if tier == SubscriptionTier.CANCELLED:
        return Entitlement(
            ACCESS_CANCELLED, None, False, False, 0, trial.max_members, _blocked_message(ACCESS_CANCELLED)
        )

    trial_end = _parse_iso(_billing_get(billing, "trialEndDate"))
    if trial_end is not None and now > trial_end:
        return Entitlement(
            ACCESS_TRIAL_EXPIRED, None, False, False, 0, trial.max_members,
            _blocked_message(ACCESS_TRIAL_EXPIRED),
        )
    return Entitlement(ACCESS_TRIAL, None, True, False, trial.upload_limit, trial.max_members)


def has_live_subscription(billing) -> bool:
    """True when the billing record holds an active/trialing/past_due Team subscription."""
    return (
        _billing_get(billing, "subscriptionTier") == SubscriptionTier.TEAM
        and _billing_get(billing, "billingStatus") in LIVE_BILLING_STATUSES
    )


def _trial_limit_message(limit: int) -> str:
    next_step = "Please upgrade to continue." if _billing_ui() else "Please contact support to continue."
    return f"You've reached the trial limit of {limit} contract uploads. {next_step}"


async def get_or_create_billing(org_id: str, database=None) -> OrganizationBilling:
    """Get existing billing record or create a new one for trial.

    ``database`` lets Celery workers (which have their own per-task Motor
    handle) use the same logic; it defaults to the API's database.
    """
    database = db if database is None else database
    billing = await database.organization_billing.find_one({"organizationId": org_id})
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
    try:
        await database.organization_billing.insert_one(new_billing.model_dump())
    except DuplicateKeyError:
        # Concurrent first access (unique index on organizationId): use the winner.
        existing = await database.organization_billing.find_one({"organizationId": org_id})
        if existing:
            return OrganizationBilling(**existing)
        raise
    return new_billing


async def get_entitlement(org_id: str) -> Entitlement:
    return compute_entitlement(await get_or_create_billing(org_id))


async def ensure_ai_access(org_id: str) -> Entitlement:
    """Raise 403 unless the org may spend AI credits (active trial or paid)."""
    entitlement = await get_entitlement(org_id)
    if not entitlement.can_use_ai:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=entitlement.message)
    return entitlement


async def ensure_invite_access(org_id: str) -> Entitlement:
    """Raise 403 when the org is read-only (past due beyond the grace period).

    Only the read-only state blocks invitations; trial / cancelled orgs keep
    today's behaviour (seat caps still apply via the member limit).
    """
    entitlement = await get_entitlement(org_id)
    if entitlement.read_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=entitlement.message)
    return entitlement


#: Recorded status for background AI work skipped by the entitlement gate.
SKIPPED_NO_ENTITLEMENT = "skipped"


async def background_ai_entitlement(org_id: Optional[str], database, task: str) -> Optional[Entitlement]:
    """Entitlement gate for background (Celery) AI work.

    Returns the entitlement when the org may use AI, otherwise logs the skip
    and returns ``None`` -- callers skip the work and record a skipped status
    instead of raising (a 403 makes no sense in a worker, and retrying would
    not help).
    """
    if not org_id:
        logger.warning("Skipping %s: no organization id", task)
        return None
    entitlement = compute_entitlement(await get_or_create_billing(org_id, database))
    if not entitlement.can_use_ai:
        logger.info(
            "Skipping %s for org %s: no AI entitlement (state=%s)", task, org_id, entitlement.state
        )
        return None
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


def _stripe_has_live_subscription(customer_id: str) -> bool:
    subs = stripe.Subscription.list(customer=customer_id, status="all", limit=20)
    return any(sub.get("status") in LIVE_STRIPE_STATUSES for sub in (subs.get("data") or []))


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
        readOnly=entitlement.read_only,
        billingUiEnabled=billing_ui_enabled(),
    )


@router.get("/config")
async def get_billing_config():
    """Runtime billing flags for the frontend (no auth: nothing sensitive).

    ``billingUiEnabled`` mirrors BILLING_UI_ENABLED (default off). While off the
    frontend hides the Billing page, its nav entry, Upgrade buttons and every
    price display.
    """
    return {"billingUiEnabled": billing_ui_enabled()}


def resolve_checkout_price(request: CheckoutSessionRequest) -> Tuple[PlanConfig, str]:
    """Map the client's (planId, interval) onto a server-side allow-listed price.

    The client never chooses a Stripe price; the price comes from the plan
    config in ``utils.plans``.
    """
    plan = get_paid_plan(request.planId)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown plan.")
    price_id = plan.price_for_interval(request.interval)
    if price_id:
        return plan, price_id
    if request.interval != "monthly" and plan.default_price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{request.interval.capitalize()} billing isn't available for this plan.",
        )
    logger.error("No Stripe price configured for plan %s (%s)", plan.id, request.interval)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Billing not configured. Please contact support.",
    )


#: Stripe Checkout rejects a ``subscription_data.trial_end`` less than 48 hours
#: in the future.
STRIPE_MIN_TRIAL_END = timedelta(hours=48)
#: Headroom so latency / clock skew between us and Stripe can't push a
#: borderline trial_end under Stripe's minimum.
_TRIAL_END_SAFETY_MARGIN = timedelta(minutes=1)


def checkout_trial_end(billing, now: Optional[datetime] = None) -> Optional[int]:
    """Unix ``trial_end`` that carries the remaining in-app trial into Stripe.

    Adding a card mid-trial must not charge the customer before the in-app
    trial would have ended, so the subscription starts trialing until that
    same moment (one trial, no extra ``trial_period_days``). ``None`` when
    the org isn't on an active in-app trial, or when less than Stripe's
    48-hour minimum is left (the customer is then billed at checkout).
    """
    now = now or datetime.now(timezone.utc)
    if compute_entitlement(billing, now).state != ACCESS_TRIAL:
        return None
    trial_end = _parse_iso(_billing_get(billing, "trialEndDate"))
    if trial_end is None:
        return None
    if trial_end - now < STRIPE_MIN_TRIAL_END + _TRIAL_END_SAFETY_MARGIN:
        return None
    return int(trial_end.timestamp())


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

    # One subscription per org: a second checkout would bill the customer twice.
    if has_live_subscription(billing):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_ALREADY_SUBSCRIBED)
    
    try:
        # Webhooks can lag behind a just-completed checkout, so also ask Stripe
        # whether this customer already has a live subscription.
        if billing.stripeCustomerId and _stripe_has_live_subscription(billing.stripeCustomerId):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_ALREADY_SUBSCRIBED)

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

        # Single trial: no trial_period_days. A card added mid-trial only
        # defers the first charge to the end of the in-app trial.
        subscription_data = {"metadata": {"organizationId": org_id, "planId": plan.id}}
        trial_end = checkout_trial_end(billing)
        if trial_end is not None:
            subscription_data["trial_end"] = trial_end

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
            subscription_data=subscription_data,
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


def _invoice_subscription_id(invoice) -> Optional[str]:
    """Subscription an invoice belongs to (pre- and post-2025 Stripe API shapes)."""
    sub = invoice.get("subscription")
    if not sub:
        parent = invoice.get("parent") or {}
        sub = ((parent.get("subscription_details") or {}).get("subscription")) if parent else None
    if sub is not None and not isinstance(sub, str):  # expanded object
        sub = sub.get("id")
    return sub or None


def _is_foreign_subscription(billing: dict, subscription_id: Optional[str]) -> bool:
    """True when ``subscription_id`` is not the org's current subscription."""
    current = billing.get("stripeSubscriptionId")
    return bool(subscription_id and current and subscription_id != current)


async def handle_subscription_deleted(subscription, event_at: Optional[int] = None):
    """Handle subscription cancellation"""
    org_id = await _org_for_subscription(subscription)
    if not org_id:
        return
    billing = await db.organization_billing.find_one({"organizationId": org_id})
    if not billing:
        return
    # Only the org's *current* subscription ending cancels the org. Deleting a
    # duplicate (or an abandoned incomplete one) must leave the org untouched.
    if subscription.get("id") != billing.get("stripeSubscriptionId"):
        logger.warning(
            "Ignoring deletion of subscription %s for org %s: current subscription is %s",
            subscription.get("id"), org_id, billing.get("stripeSubscriptionId"),
        )
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
    if _is_foreign_subscription(billing, _invoice_subscription_id(invoice)):
        logger.warning(
            "Ignoring payment failure for subscription %s (org %s, current %s)",
            _invoice_subscription_id(invoice), billing["organizationId"], billing.get("stripeSubscriptionId"),
        )
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
    if _is_foreign_subscription(billing, _invoice_subscription_id(invoice)):
        logger.warning(
            "Ignoring paid invoice for subscription %s (org %s, current %s)",
            _invoice_subscription_id(invoice), billing["organizationId"], billing.get("stripeSubscriptionId"),
        )
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
    if current is None:
        logger.warning("Subscription %s references org %s with no billing record", subscription.get("id"), org_id)
        return
    # Org scoping: the subscription's customer must be the org's customer.
    if current.get("stripeCustomerId") and customer_id and customer_id != current["stripeCustomerId"]:
        logger.error(
            "Subscription %s (customer %s) does not belong to org %s (customer %s); not applied",
            subscription.get("id"), customer_id, org_id, current["stripeCustomerId"],
        )
        return
    # Duplicate guard: while the org has a live subscription, events for any
    # other subscription id must not overwrite it. (A new subscription is
    # accepted once the current one is incomplete / cancelled / deleted.)
    if _is_foreign_subscription(current, subscription.get("id")) and has_live_subscription(current):
        logger.error(
            "Duplicate subscription %s for org %s (current %s is %s); not applied -- "
            "cancel/refund it in Stripe",
            subscription.get("id"), org_id, current.get("stripeSubscriptionId"), current.get("billingStatus"),
        )
        return
    
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
    if plan is None and billing_status in LIVE_BILLING_STATUSES:
        logger.error(
            "Subscription %s for org %s uses Stripe price %r which is not in the plan "
            "config; recorded, but it grants NO paid access",
            subscription.get("id"), org_id, price_id,
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


def _plan_prices(plan: PlanConfig) -> dict:
    """Display prices per checkout interval, reconciled with Stripe (B9)."""
    prices = {}
    for checkout_interval in CHECKOUT_INTERVALS:
        shown = reconcile_display_price(plan, checkout_interval)
        if shown is not None:
            prices[checkout_interval] = shown.as_dict()
    return prices


@router.get("/plans")
async def get_plans():
    """Get available pricing plans (rendered from the server-side plan config).

    Paid-plan prices are checked against the Stripe Price objects for the
    configured price IDs; on a mismatch Stripe's amount is served (and an error
    logged), so the page never advertises a price other than the one charged.
    """
    trial = get_trial_config()
    paid_plans = list(get_paid_plans().values())
    # Stripe lookups are blocking network calls (cached); keep them off the loop.
    paid_prices = await asyncio.to_thread(lambda: [_plan_prices(p) for p in paid_plans])
    plans = [
        {
            "id": "trial",
            "name": "Free Trial",
            "price": 0,
            "currency": paid_plans[0].currency if paid_plans else "usd",
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
    for plan, prices in zip(paid_plans, paid_prices):
        monthly = prices.get("monthly")
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
            # Monthly price (Stripe-verified when possible); ``prices`` has
            # every purchasable interval.
            "price": monthly["amount"] if monthly else None,
            "currency": monthly["currency"] if monthly else plan.currency,
            "interval": monthly["interval"] if monthly else plan.interval,
            "prices": prices,
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
            # Intervals the client may request at checkout (no price IDs).
            "intervals": [i for i in CHECKOUT_INTERVALS if plan.price_for_interval(i) and i in prices],
        })
    return {"plans": plans}


# Export the check function for use in contracts route
__all__ = [
    "router",
    "check_trial_access",
    "compute_entitlement",
    "has_live_subscription",
    "ensure_ai_access",
    "background_ai_entitlement",
    "SKIPPED_NO_ENTITLEMENT",
    "get_entitlement",
    "Entitlement",
    "reserve_trial_upload_atomic",
    "release_trial_upload",
    "increment_trial_usage",
    "get_or_create_billing",
    "init_db",
]
