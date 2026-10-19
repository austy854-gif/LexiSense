// Billing page call-to-action rules (B6). Every action is either a working
// button or plain text -- never an enabled button that does nothing -- and
// "Current plan" is only shown for a plan the org actually has right now.
//
// accessState comes from the backend entitlement (GET /billing/subscription):
// trial | trial_expired | paid | past_due_grace | past_due | cancelled |
// inactive | unverified_plan.

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
    if (status === 'past_due') return 'past_due';
    if (status === 'cancelled') return 'cancelled';
    return 'inactive';
  }
  const end = subscription.trialEndDate ? new Date(subscription.trialEndDate) : null;
  return end && end < new Date() ? 'trial_expired' : 'trial';
}

/**
 * The main billing action for the org.
 * Returns { kind, label, description? } where kind is one of CTA_*.
 */
export function getPrimaryAction(subscription, { isAdmin }) {
  const state = accessStateOf(subscription);
  if (!state) return { kind: CTA_NONE, label: '' };
  if (!isAdmin) {
    return {
      kind: CTA_NONE,
      label: '',
      description: 'Only an organization admin can change billing.',
    };
  }
  switch (state) {
    case 'trial':
      return { kind: CTA_CHECKOUT, label: 'Upgrade to Team plan' };
    case 'trial_expired':
      return { kind: CTA_CHECKOUT, label: 'Subscribe to Team plan' };
    case 'cancelled':
      return { kind: CTA_CHECKOUT, label: 'Resubscribe to Team plan' };
    case 'paid':
      return { kind: CTA_PORTAL, label: 'Manage subscription' };
    case 'past_due_grace':
    case 'past_due':
      return { kind: CTA_PORTAL, label: 'Update payment method' };
    case 'inactive':
      return subscription?.stripeCustomerId
        ? { kind: CTA_PORTAL, label: 'Manage subscription' }
        : { kind: CTA_CONTACT, label: 'Contact support' };
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
 *  - action: the primary action when it applies to this card (checkout only)
 */
export function getPlanCardState(plan, subscription, { isAdmin }) {
  const state = accessStateOf(subscription);
  const primary = getPrimaryAction(subscription, { isAdmin });
  if (plan.id === 'trial') {
    // The trial can't be bought or restarted: never a button.
    if (state === 'trial') return { show: true, label: 'Current plan' };
    if (state === 'trial_expired') return { show: true, label: 'Trial ended' };
    return { show: false };
  }
  if (state === 'paid') return { show: true, label: 'Current plan' };
  if (state === 'past_due_grace' || state === 'past_due') {
    return { show: true, label: 'Current plan · payment failed' };
  }
  if (primary.kind === CTA_CHECKOUT) return { show: true, action: primary };
  return { show: true };
}

const ZERO_DECIMAL = new Set(['bif', 'clp', 'djf', 'gnf', 'jpy', 'kmf', 'krw', 'mga', 'pyg', 'rwf', 'ugx', 'vnd', 'vuv', 'xaf', 'xof', 'xpf']);

/** "$49", "$49.99", "CA$59" ... from the server's amount + currency. */
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

export function intervalLabel(interval) {
  return interval === 'year' ? 'year' : 'month';
}
