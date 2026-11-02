// Billing page call-to-action rules (B6), ported from fix/billing-integrity and
// adapted to Option B (several paid plans, checkout by planId + interval).
// Every action is either a working button or plain text -- never an enabled
// button that does nothing -- and "Current plan" is only shown for the plan the
// org actually has right now.
//
// accessState comes from the backend entitlement (GET /billing/subscription):
// trial | trial_expired | paid | grace | past_due | cancelled | unverified_plan.

export const CTA_CHECKOUT = 'checkout';
export const CTA_PORTAL = 'portal';
export const CTA_CONTACT = 'contact';
export const CTA_NONE = 'none';

/** Fallback for responses that predate accessState. */
export function accessStateOf(subscription) {
  if (!subscription) return null;
  if (subscription.accessState) return subscription.accessState;
  const { subscriptionTier: tier, billingStatus: status } = subscription;
  if (tier === 'cancelled') return 'cancelled';
  if (tier === 'team') {
    if (status === 'active' || status === 'trialing') return 'paid';
    if (status === 'past_due') return subscription.readOnly ? 'past_due' : 'grace';
    if (status === 'cancelled') return 'cancelled';
    return 'unverified_plan';
  }
  const end = subscription.trialEndDate ? new Date(subscription.trialEndDate) : null;
  return end && end < new Date() ? 'trial_expired' : 'trial';
}

const CHECKOUT_STATES = new Set(['trial', 'trial_expired', 'cancelled']);

/**
 * The main billing action for the org (not tied to a plan card).
 * Returns { kind, label, description? } where kind is one of CTA_*.
 * Checkout states return CTA_NONE here: the plan cards carry the checkout buttons.
 */
export function getPrimaryAction(subscription, { isAdmin }) {
  const state = accessStateOf(subscription);
  if (!state) return { kind: CTA_NONE, label: '' };
  if (!isAdmin) {
    return { kind: CTA_NONE, label: '', description: 'Only an organization admin can change billing.' };
  }
  switch (state) {
    case 'trial':
    case 'trial_expired':
    case 'cancelled':
      return { kind: CTA_NONE, label: '', description: 'Choose a plan below.' };
    case 'paid':
      return { kind: CTA_PORTAL, label: 'Manage subscription' };
    case 'grace':
    case 'past_due':
      return { kind: CTA_PORTAL, label: 'Update payment method' };
    case 'unverified_plan':
    default:
      return {
        kind: CTA_CONTACT,
        label: 'Contact support',
        description: "We couldn't verify your subscription plan. Please contact support.",
      };
  }
}

/**
 * What a plan card shows: { show, label?, action? }.
 *  - label: non-interactive status text ("Current plan", "Trial ended")
 *  - action: { kind: CTA_CHECKOUT, label, planId } when this card can be bought
 */
export function getPlanCardState(plan, subscription, { isAdmin }) {
  const state = accessStateOf(subscription);
  if (plan.id === 'trial') {
    // The trial can't be bought or restarted: never a button.
    if (state === 'trial') return { show: true, label: 'Current plan' };
    if (state === 'trial_expired') return { show: true, label: 'Trial ended' };
    return { show: false };
  }
  const isCurrent = subscription?.planId === plan.id;
  if (state === 'paid') return { show: true, label: isCurrent ? 'Current plan' : undefined };
  if (state === 'grace' || state === 'past_due') {
    return { show: true, label: isCurrent ? 'Current plan · payment failed' : undefined };
  }
  if (isAdmin && CHECKOUT_STATES.has(state) && plan.available !== false) {
    return { show: true, action: { kind: CTA_CHECKOUT, label: plan.cta, planId: plan.id } };
  }
  return { show: true };
}

const ZERO_DECIMAL = new Set(['bif', 'clp', 'djf', 'gnf', 'jpy', 'kmf', 'krw', 'mga', 'pyg', 'rwf', 'ugx', 'vnd', 'vuv', 'xaf', 'xof', 'xpf']);

/** Server amount + currency -> display string. Never a hardcoded price. */
export function formatPrice(amount, currency = 'usd') {
  if (amount === null || amount === undefined) return '';
  const code = String(currency || 'usd').toUpperCase();
  const whole = Number.isInteger(amount) || ZERO_DECIMAL.has(code.toLowerCase());
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: code,
      minimumFractionDigits: whole ? 0 : 2,
      maximumFractionDigits: whole ? 0 : 2,
    }).format(amount);
  } catch {
    return `${amount} ${code}`;
  }
}

/** "USD, tax included" etc. from /billing/plans; falls back to the taxInclusive flag. */
export function priceLabelOf(plan) {
  if (!plan) return '';
  if (plan.priceLabel) return plan.priceLabel;
  return plan.taxInclusive ? 'Tax included' : '';
}
