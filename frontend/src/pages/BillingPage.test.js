import { act } from 'react';
import { createRoot } from 'react-dom/client';
import BillingPage from './BillingPage';
import { billingAPI } from '../api';
import { useAuth } from '../contexts/AuthContext';

jest.mock('../api', () => ({
  billingAPI: {
    getSubscription: jest.fn(),
    getPlans: jest.fn(),
    createCheckout: jest.fn(),
    createPortal: jest.fn(),
  },
}));
jest.mock('../contexts/AuthContext', () => ({ useAuth: jest.fn() }));
jest.mock('../components/Layout', () => ({ Layout: ({ children }) => children }));
jest.mock('sonner', () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const PLANS = [
  {
    id: 'trial', name: 'Free Trial', price: 0, currency: 'usd', interval: 'month',
    trialDays: 14, uploadLimit: 25, memberLimit: 3, features: ['Free trial'], cta: 'Start Free Trial',
  },
  {
    id: 'team', name: 'Team Plan', price: 59, currency: 'usd', interval: 'month', memberLimit: 12,
    prices: {
      monthly: { amount: 59, currency: 'usd', interval: 'month', verified: true },
      annual: { amount: 590, currency: 'usd', interval: 'year', verified: true },
    },
    intervals: ['monthly', 'annual'], features: ['Unlimited contract uploads'], cta: 'Upgrade to Team',
  },
];

const STATES = {
  trial: { subscriptionTier: 'trial', billingStatus: 'trialing', accessState: 'trial', trialEndDate: '2999-01-01T00:00:00Z', trialContractsUsed: 5, trialContractsLimit: 25 },
  trial_expired: { subscriptionTier: 'trial', billingStatus: 'trialing', accessState: 'trial_expired', trialEndDate: null, trialContractsUsed: 25, trialContractsLimit: 25 },
  cancelled: { subscriptionTier: 'cancelled', billingStatus: 'cancelled', accessState: 'cancelled', stripeCustomerId: 'cus_1' },
  paid: { subscriptionTier: 'team', billingStatus: 'active', accessState: 'paid', stripeCustomerId: 'cus_1', currentPeriodEnd: '2026-11-19T00:00:00Z' },
  past_due_grace: { subscriptionTier: 'team', billingStatus: 'past_due', accessState: 'past_due_grace', stripeCustomerId: 'cus_1', warning: 'Your last payment failed. Please update within 3 days.' },
  past_due: { subscriptionTier: 'team', billingStatus: 'past_due', accessState: 'past_due', readOnly: true, stripeCustomerId: 'cus_1' },
};

let container;
let root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  jest.spyOn(console, 'error').mockImplementation(() => {});
  useAuth.mockReturnValue({ user: { role: 'admin' } });
  billingAPI.getPlans.mockResolvedValue({ data: { plans: PLANS } });
  billingAPI.createCheckout.mockResolvedValue({ data: { url: '#checkout' } });
  billingAPI.createPortal.mockResolvedValue({ data: { url: '#portal' } });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.restoreAllMocks();
  Object.values(billingAPI).forEach((fn) => fn.mockReset());
});

async function render(state) {
  billingAPI.getSubscription.mockResolvedValue({ data: STATES[state] });
  await act(async () => {
    root.render(<BillingPage />);
  });
}

const q = (id) => container.querySelector(`[data-testid="${id}"]`);
const buttons = () => Array.from(container.querySelectorAll('button'));

async function click(el) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

test.each([
  ['trial', 'Upgrade to Team plan'],
  ['trial_expired', 'Subscribe to Team plan'],
  ['cancelled', 'Resubscribe to Team plan'],
  ['paid', 'Manage subscription'],
  ['past_due_grace', 'Update payment method'],
  ['past_due', 'Update payment method'],
])('%s: primary action is "%s" and every button is wired', async (state, label) => {
  await render(state);
  expect(q('billing-primary-action').textContent).toContain(label);
  for (const b of buttons()) {
    // No enabled-but-inert buttons: each one has a handler React attached.
    const propsKey = Object.keys(b).find((k) => k.startsWith('__reactProps'));
    expect(typeof b[propsKey].onClick).toBe('function');
  }
});

test('checkout sends planId + interval, never a price ID', async () => {
  await render('trial');
  await click(q('billing-primary-action'));
  expect(billingAPI.createCheckout).toHaveBeenCalledWith(
    { planId: 'team', interval: 'monthly' }, expect.any(String), expect.any(String),
  );
  const annual = buttons().find((b) => b.textContent === 'Billed annually');
  await click(annual);
  expect(q('plan-price-team').textContent).toBe('$590/year');
  await click(q('plan-action-team'));
  expect(billingAPI.createCheckout).toHaveBeenLastCalledWith(
    { planId: 'team', interval: 'annual' }, expect.any(String), expect.any(String),
  );
  expect(JSON.stringify(billingAPI.createCheckout.mock.calls)).not.toMatch(/price_|priceId/);
});

test.each(['paid', 'past_due', 'past_due_grace'])('%s uses the billing portal, not checkout', async (state) => {
  await render(state);
  await click(q('billing-primary-action'));
  expect(billingAPI.createPortal).toHaveBeenCalledTimes(1);
  expect(billingAPI.createCheckout).not.toHaveBeenCalled();
});

test('trial card shows Current plan only during the trial, never as a button', async () => {
  await render('trial');
  expect(q('plan-label-trial').textContent).toContain('Current plan');
  expect(q('plan-card-trial').querySelector('button')).toBeNull();
});

test('after the trial ends there is no "Current plan" anywhere', async () => {
  await render('trial_expired');
  expect(container.textContent).not.toMatch(/Current plan/i);
  expect(q('plan-label-trial').textContent).toContain('Trial ended');
  expect(q('plan-card-trial').querySelector('button')).toBeNull();
  expect(q('plan-action-team').textContent).toContain('Subscribe to Team plan');
});

test.each(['cancelled', 'paid', 'past_due'])('%s: trial card is hidden', async (state) => {
  await render(state);
  expect(q('plan-card-trial')).toBeNull();
});

test('paid: team card says Current plan without a button', async () => {
  await render('paid');
  expect(q('plan-label-team').textContent).toContain('Current plan');
  expect(q('plan-card-team').querySelector('button')).toBeNull();
});

test('past due after grace explains read-only access', async () => {
  await render('past_due');
  expect(q('past-due-notice').textContent).toMatch(/read-only/);
  expect(q('past-due-notice').textContent).toMatch(/view and download/);
});

test('non-admins see an explanation instead of buttons', async () => {
  useAuth.mockReturnValue({ user: { role: 'manager' } });
  await render('trial');
  expect(q('billing-primary-action').textContent).toMatch(/admin/);
  expect(q('plan-card-team').querySelector('button')).toBeNull();
});

test('limits and prices come from /billing/plans; no trial length, no hard-coded 3 or $49', async () => {
  await render('trial');
  const text = container.textContent;
  expect(text).toContain('5 / 25 contracts used');
  expect(text).toContain('25 per trial');
  expect(text).toContain('When your trial ends or you reach the 25-contract limit');
  expect(text).not.toMatch(/\d+-day/); // trial length is never stated
  expect(text).toContain('flat $59/month for your entire team (up to 12 members)');
  expect(text).not.toMatch(/7-day|\$49|3-contract|3 per trial/);
});

test('a failed load shows an error with retry instead of dead buttons', async () => {
  billingAPI.getSubscription.mockRejectedValueOnce(new Error('Network Error'));
  await act(async () => {
    root.render(<BillingPage />);
  });
  expect(q('billing-load-error')).not.toBeNull();
  billingAPI.getSubscription.mockResolvedValue({ data: STATES.trial });
  await click(buttons().find((b) => b.textContent.includes('Try again')));
  expect(q('billing-primary-action').textContent).toContain('Upgrade to Team plan');
});

test('no purchasable interval: explanation instead of a checkout button', async () => {
  billingAPI.getPlans.mockResolvedValue({
    data: { plans: [PLANS[0], { ...PLANS[1], intervals: [], prices: {} }] },
  });
  await render('trial');
  expect(q('billing-primary-action').tagName).toBe('P');
  expect(q('billing-primary-action').textContent).toMatch(/contact support/);
  expect(q('plan-card-team').querySelector('button')).toBeNull();
});
