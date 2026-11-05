// Frontend mirror of backend/utils/plans.py (plan ids, names, member caps).
// Keep in sync with plans.py: PAID_PLAN_IDS, _NAMES and _MEMBER_DEFAULTS.
//
// plans.py does NOT hard-code prices: checkout and /billing/plans read them
// from the Stripe Price objects (STRIPE_PRICE_<PLAN>_<INTERVAL>). The amounts
// below are the approved Option B list prices (whole USD per month) used for
// the public landing page only; they must match the Stripe prices.
export const BILLING_INTERVALS = ['monthly', 'annual'];

export const PAID_PLANS = [
  { id: 'starter', name: 'Starter', maxMembers: 5, prices: { monthly: 49, annual: 39 } },
  { id: 'team', name: 'Team', maxMembers: 15, prices: { monthly: 129, annual: 99 }, highlight: true },
  { id: 'business', name: 'Business', maxMembers: 30, prices: { monthly: 249, annual: 199 } },
];

export const PAID_PLAN_NAMES = PAID_PLANS.map((p) => p.name);

/** "Starter, Team or Business" / "Starter, Team and Business". */
export function planNameList(conjunction = 'or') {
  const names = PAID_PLAN_NAMES;
  return `${names.slice(0, -1).join(', ')} ${conjunction} ${names[names.length - 1]}`;
}
