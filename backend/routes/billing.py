from fastapi import APIRouter, HTTPException, status, Depends, Request
from typing import Optional
from datetime import datetime, timezone, timedelta
import logging
import os
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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["Billing"])

db = None
stripe_api_key = None

def init_db(database):
    global db, stripe_api_key
    db = database
    stripe_api_key = os.environ.get("STRIPE_SECRET_KEY")
    if stripe_api_key:
        stripe.api_key = stripe_api_key
    else:
        logger.warning("STRIPE_SECRET_KEY not set - billing will not work")


TRIAL_DAYS = 7
TRIAL_CONTRACT_LIMIT = 3
TEAM_PLAN_PRICE_ID = os.environ.get("STRIPE_TEAM_PRICE_ID")  # price_... from Stripe Dashboard


async def get_or_create_billing(org_id: str) -> OrganizationBilling:
    """Get existing billing record or create a new one for trial"""
    billing = await db.organization_billing.find_one({"organizationId": org_id})
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
    await db.organization_billing.insert_one(new_billing.model_dump())
    return new_billing


async def check_trial_access(org_id: str) -> tuple[bool, str]:
    """Check if organization can upload contracts (trial limits)"""
    billing = await get_or_create_billing(org_id)
    
    # If paid subscription, always allow
    if billing.subscriptionTier == SubscriptionTier.TEAM and billing.billingStatus == BillingStatus.ACTIVE:
        return True, ""
    
    # Check if trial has expired
    if billing.trialEndDate:
        trial_end = datetime.fromisoformat(billing.trialEndDate.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > trial_end:
            return False, "Your 7-day free trial has ended. Please upgrade to continue using LexiSense."
    
    # Check contract upload limit
    if billing.trialContractsUsed >= billing.trialContractsLimit:
        return False, f"You've reached the trial limit of {TRIAL_CONTRACT_LIMIT} contract uploads. Please upgrade to continue."
    
    return True, ""


async def increment_trial_usage(org_id: str):
    """Increment trial contract usage counter"""
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
    
    return SubscriptionInfo(
        subscriptionTier=billing.subscriptionTier,
        billingStatus=billing.billingStatus,
        trialEndDate=billing.trialEndDate if not is_trial_expired else None,
        currentPeriodEnd=billing.currentPeriodEnd,
        cancelAtPeriodEnd=billing.cancelAtPeriodEnd,
        trialContractsUsed=billing.trialContractsUsed,
        trialContractsLimit=billing.trialContractsLimit,
        stripeCustomerId=billing.stripeCustomerId,
        stripeSubscriptionId=billing.stripeSubscriptionId
    )


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
    
    org_id = current_user["organizationId"]
    billing = await get_or_create_billing(org_id)
    
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
                "price": request.priceId or TEAM_PLAN_PRICE_ID,
                "quantity": 1,
            }],
            mode="subscription",
            success_url=request.successUrl,
            cancel_url=request.cancelUrl,
            subscription_data={
                "trial_period_days": TRIAL_DAYS,
                "metadata": {"organizationId": org_id}
            },
            metadata={"organizationId": org_id}
        )
        return CheckoutSessionResponse(sessionId=session.id, url=session.url)
    except stripe.error.StripeError as e:
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
    except stripe.error.StripeError as e:
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
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    
    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        raise HTTPException(status_code=500, detail="Webhook not configured")
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError as e:
        logger.error(f"Invalid payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
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
    
    await db.organization_billing.update_one(
        {"organizationId": org_id},
        {
            "$set": {
                "subscriptionTier": SubscriptionTier.CANCELLED,
                "billingStatus": BillingStatus.CANCELLED,
                "cancelledAt": datetime.now(timezone.utc).isoformat(),
                "stripeSubscriptionId": None,
                "updatedAt": datetime.now(timezone.utc).isoformat()
            }
        }
    )


async def handle_payment_failed(invoice):
    """Handle failed payment"""
    customer_id = invoice.get("customer")
    billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
    if not billing:
        return
    
    await db.organization_billing.update_one(
        {"organizationId": billing["organizationId"]},
        {"$set": {"billingStatus": BillingStatus.PAST_DUE, "updatedAt": datetime.now(timezone.utc).isoformat()}}
    )


async def handle_invoice_paid(invoice):
    """Handle successful invoice payment"""
    customer_id = invoice.get("customer")
    billing = await db.organization_billing.find_one({"stripeCustomerId": customer_id})
    if not billing:
        return
    
    # If was past_due, restore to active
    if billing.get("billingStatus") == BillingStatus.PAST_DUE:
        await db.organization_billing.update_one(
            {"organizationId": billing["organizationId"]},
            {"$set": {"billingStatus": BillingStatus.ACTIVE, "updatedAt": datetime.now(timezone.utc).isoformat()}}
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
    
    # Determine tier
    if billing_status == BillingStatus.ACTIVE or billing_status == BillingStatus.TRIALING:
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
    
    await db.organization_billing.update_one(
        {"organizationId": org_id},
        {
            "$set": {
                "stripeCustomerId": customer_id,
                "stripeSubscriptionId": subscription.get("id"),
                "stripePriceId": subscription.get("items", {}).get("data", [{}])[0].get("price", {}).get("id"),
                "subscriptionTier": tier,
                "billingStatus": billing_status,
                "currentPeriodStart": datetime.fromtimestamp(subscription.get("current_period_start", 0), tz=timezone.utc).isoformat() if subscription.get("current_period_start") else None,
                "currentPeriodEnd": current_period_end,
                "trialEndDate": trial_end,
                "cancelAtPeriodEnd": subscription.get("cancel_at_period_end", False),
                "updatedAt": datetime.now(timezone.utc).isoformat()
            }
        }
    )


@router.get("/plans")
async def get_plans():
    """Get available pricing plans"""
    return {
        "plans": [
            {
                "id": "trial",
                "name": "Free Trial",
                "price": 0,
                "interval": "month",
                "features": [
                    f"{TRIAL_DAYS}-day free trial",
                    f"Up to {TRIAL_CONTRACT_LIMIT} contract uploads",
                    "AI contract analysis",
                    "Contract repository",
                    "Renewal alerts",
                    "Basic dashboard"
                ],
                "cta": "Start Free Trial"
            },
            {
                "id": "team",
                "name": "Team Plan",
                "price": 49,
                "interval": "month",
                "features": [
                    "Unlimited contract uploads",
                    "AI contract analysis",
                    "Contract repository",
                    "Renewal alerts",
                    "Advanced dashboard & analytics",
                    "Team collaboration (up to 10 members)",
                    "Role-based access control",
                    "Approval workflows",
                    "Contract templates library",
                    "PDF export",
                    "Audit logs",
                    "Priority support"
                ],
                "cta": "Upgrade to Team",
                "priceId": TEAM_PLAN_PRICE_ID
            }
        ]
    }


# Export the check function for use in contracts route
__all__ = ["router", "check_trial_access", "increment_trial_usage", "get_or_create_billing", "init_db"]