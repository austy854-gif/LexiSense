import {
  CTA_CHECKOUT, CTA_CONTACT, CTA_NONE, CTA_PORTAL,
  accessStateOf, formatPrice, getPlanCardState, getPrimaryAction, priceLabelOf,
} from './billingCta';

const team = { id: 'team', cta: 'Upgrade to Team', available: true };
const starter = { id: 'starter', cta: 'Upgrade to Starter', available: true };
const trial = { id: 'trial' };
const admin = { isAdmin: true };

test.each([
  ['paid', CTA_PORTAL, 'Manage subscription'],
  ['grace', CTA_PORTAL, 'Update payment method'],
  ['past_due', CTA_PORTAL, 'Update payment method'],
  ['unverified_plan', CTA_CONTACT, 'Contact support'],
  ['trial', CTA_NONE, ''],
  ['trial_expired', CTA_NONE, ''],
  ['cancelled', CTA_NONE, ''],
])('primary action for %s', (accessState, kind, label) => {
  expect(getPrimaryAction({ accessState }, admin)).toMatchObject({ kind, label });
});

test('non-admins get no action', () => {
  expect(getPrimaryAction({ accessState: 'paid' }, { isAdmin: false }).kind).toBe(CTA_NONE);
  expect(getPlanCardState(team, { accessState: 'trial' }, { isAdmin: false }).action).toBeUndefined();
});

test('trial and cancelled orgs can check out any available plan by planId', () => {
  for (const accessState of ['trial', 'trial_expired', 'cancelled']) {
    const card = getPlanCardState(starter, { accessState }, admin);
    expect(card.action).toEqual({ kind: CTA_CHECKOUT, label: 'Upgrade to Starter', planId: 'starter' });
  }
  expect(getPlanCardState({ ...team, available: false }, { accessState: 'trial' }, admin).action).toBeUndefined();
});

test('"Current plan" only on the plan the org has', () => {
  const sub = { accessState: 'paid', planId: 'team' };
  expect(getPlanCardState(team, sub, admin)).toEqual({ show: true, label: 'Current plan' });
  expect(getPlanCardState(starter, sub, admin)).toEqual({ show: true, label: undefined });
  expect(getPlanCardState(team, { ...sub, accessState: 'past_due' }, admin).label).toMatch(/payment failed/);
});

test('trial card is text only', () => {
  expect(getPlanCardState(trial, { accessState: 'trial' }, admin)).toEqual({ show: true, label: 'Current plan' });
  expect(getPlanCardState(trial, { accessState: 'trial_expired' }, admin)).toEqual({ show: true, label: 'Trial ended' });
  expect(getPlanCardState(trial, { accessState: 'paid' }, admin)).toEqual({ show: false });
});

test('accessStateOf falls back for old responses', () => {
  expect(accessStateOf({ subscriptionTier: 'team', billingStatus: 'active' })).toBe('paid');
  expect(accessStateOf({ subscriptionTier: 'team', billingStatus: 'past_due', readOnly: true })).toBe('past_due');
  expect(accessStateOf({ subscriptionTier: 'team', billingStatus: 'past_due' })).toBe('grace');
  expect(accessStateOf({ subscriptionTier: 'cancelled' })).toBe('cancelled');
});

test('prices come from the server amount and currency', () => {
  expect(formatPrice(12, 'usd')).toBe('$12');
  expect(formatPrice(12.5, 'usd')).toBe('$12.50');
  expect(formatPrice(null)).toBe('');
});

test('tax label', () => {
  expect(priceLabelOf({ priceLabel: 'USD, tax included' })).toBe('USD, tax included');
  expect(priceLabelOf({ taxInclusive: true })).toBe('Tax included');
  expect(priceLabelOf({})).toBe('');
});
