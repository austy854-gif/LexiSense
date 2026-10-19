import {
  CTA_CHECKOUT,
  CTA_CONTACT,
  CTA_NONE,
  CTA_PORTAL,
  accessStateOf,
  formatPrice,
  getPlanCardState,
  getPrimaryAction,
} from './billingCta';

const admin = { isAdmin: true };
const sub = (accessState, extra = {}) => ({ accessState, subscriptionTier: 'trial', billingStatus: 'trialing', ...extra });
const TRIAL = { id: 'trial' };
const TEAM = { id: 'team' };

test.each([
  ['trial', CTA_CHECKOUT, 'Upgrade to Team plan'],
  ['trial_expired', CTA_CHECKOUT, 'Subscribe to Team plan'],
  ['cancelled', CTA_CHECKOUT, 'Resubscribe to Team plan'],
  ['paid', CTA_PORTAL, 'Manage subscription'],
  ['past_due_grace', CTA_PORTAL, 'Update payment method'],
  ['past_due', CTA_PORTAL, 'Update payment method'],
  ['unverified_plan', CTA_CONTACT, 'Contact support'],
])('primary action for %s', (state, kind, label) => {
  expect(getPrimaryAction(sub(state), admin)).toMatchObject({ kind, label });
});

test('inactive uses the portal only when there is a Stripe customer', () => {
  expect(getPrimaryAction(sub('inactive', { stripeCustomerId: 'cus_1' }), admin).kind).toBe(CTA_PORTAL);
  expect(getPrimaryAction(sub('inactive'), admin).kind).toBe(CTA_CONTACT);
});

test('non-admins get an explanation, never a button', () => {
  for (const state of ['trial', 'trial_expired', 'cancelled', 'paid', 'past_due']) {
    const action = getPrimaryAction(sub(state), { isAdmin: false });
    expect(action.kind).toBe(CTA_NONE);
    expect(action.description).toMatch(/admin/);
  }
});

test('trial card is a label, never a button, and says Current plan only during the trial', () => {
  expect(getPlanCardState(TRIAL, sub('trial'), admin)).toEqual({ show: true, label: 'Current plan' });
  expect(getPlanCardState(TRIAL, sub('trial_expired'), admin)).toEqual({ show: true, label: 'Trial ended' });
  for (const state of ['paid', 'cancelled', 'past_due', 'past_due_grace', 'unverified_plan']) {
    expect(getPlanCardState(TRIAL, sub(state), admin)).toEqual({ show: false });
  }
});

test('team card: checkout when the org can buy it, a label when it already has it', () => {
  expect(getPlanCardState(TEAM, sub('trial'), admin).action.kind).toBe(CTA_CHECKOUT);
  expect(getPlanCardState(TEAM, sub('trial_expired'), admin).action.kind).toBe(CTA_CHECKOUT);
  expect(getPlanCardState(TEAM, sub('cancelled'), admin).action.label).toBe('Resubscribe to Team plan');
  expect(getPlanCardState(TEAM, sub('paid'), admin)).toEqual({ show: true, label: 'Current plan' });
  expect(getPlanCardState(TEAM, sub('past_due'), admin).label).toMatch(/payment failed/);
  expect(getPlanCardState(TEAM, sub('trial'), { isAdmin: false }).action).toBeUndefined();
});

test('accessState fallback for older responses', () => {
  expect(accessStateOf({ subscriptionTier: 'team', billingStatus: 'active' })).toBe('paid');
  expect(accessStateOf({ subscriptionTier: 'team', billingStatus: 'past_due' })).toBe('past_due');
  expect(accessStateOf({ subscriptionTier: 'cancelled', billingStatus: 'cancelled' })).toBe('cancelled');
  expect(accessStateOf({ subscriptionTier: 'trial', trialEndDate: '2000-01-01T00:00:00Z' })).toBe('trial_expired');
  expect(accessStateOf({ subscriptionTier: 'trial', trialEndDate: '2999-01-01T00:00:00Z' })).toBe('trial');
  expect(accessStateOf(null)).toBeNull();
});

test('formatPrice uses the server currency', () => {
  expect(formatPrice(49, 'usd')).toBe('$49');
  expect(formatPrice(49.99, 'usd')).toBe('$49.99');
  expect(formatPrice(59, 'cad')).toBe('CA$59');
  expect(formatPrice(7000, 'jpy')).toBe('¥7,000');
  expect(formatPrice(null, 'usd')).toBe('');
});
