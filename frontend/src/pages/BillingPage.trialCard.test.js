import { act } from 'react';
import { createRoot } from 'react-dom/client';
import BillingPage from './BillingPage';
import { billingAPI } from '../api';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock('../components/Layout', () => ({ Layout: ({ children }) => <div>{children}</div> }));
jest.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 'a', role: 'admin' } }) }));
jest.mock('../api', () => ({
  __esModule: true,
  default: {},
  billingAPI: { getSubscription: jest.fn(), getPlans: jest.fn(), createCheckout: jest.fn(), createPortal: jest.fn() },
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// Shape of GET /billing/plans: the trial plan has no priceId.
const plans = [
  { id: 'trial', name: 'Free trial', price: 0, interval: 'month', features: ['Limited contract uploads'], cta: 'Start free trial' },
  { id: 'team', name: 'Team plan', price: 49, interval: 'month', features: ['Unlimited contract uploads'], cta: 'Upgrade to Team', priceId: 'price_team' },
];

let container;
let root;

async function renderWith(subscription) {
  billingAPI.getSubscription.mockResolvedValue({ data: subscription });
  billingAPI.getPlans.mockResolvedValue({ data: { plans } });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(<BillingPage />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const future = () => new Date(Date.now() + 5 * 864e5).toISOString();
const past = () => new Date(Date.now() - 864e5).toISOString();
const trialStatus = () => container.querySelector('[data-testid="trial-plan-status"]');
const buttons = () => [...container.querySelectorAll('button')].map((b) => b.textContent.trim());

test('active trial: the trial card says "Current plan" and has no button', async () => {
  await renderWith({ subscriptionTier: 'trial', billingStatus: 'trialing', trialEndDate: future() });
  expect(trialStatus().textContent).toBe('Current plan');
  expect(container.querySelector('[data-testid="plan-card-trial"] button')).toBeNull();
  expect(container.textContent).not.toMatch(/start free trial/i);
  expect(buttons()).toContain('Upgrade to Team');
});

test('ended trial: the trial card says "Trial ended" (not a disabled "Current plan")', async () => {
  await renderWith({ subscriptionTier: 'trial', billingStatus: 'trialing', trialEndDate: past() });
  expect(trialStatus().textContent).toBe('Trial ended');
  expect(container.textContent).not.toMatch(/start free trial/i);
});

test.each(['team', 'cancelled'])('%s org: the trial card is hidden', async (tier) => {
  await renderWith({ subscriptionTier: tier, billingStatus: tier === 'team' ? 'active' : 'cancelled', trialEndDate: past() });
  expect(trialStatus()).toBeNull();
  expect(container.querySelector('[data-testid="plan-card-trial"]')).toBeNull();
  expect(container.querySelector('[data-testid="plan-card-team"]')).not.toBeNull();
  expect(container.textContent).not.toMatch(/start free trial/i);
  expect(container.textContent).toContain('Team plan');
});

test('no button on the page ever checks out without a price id', async () => {
  await renderWith({ subscriptionTier: 'trial', billingStatus: 'trialing', trialEndDate: future() });
  const upgrade = [...container.querySelectorAll('button')].filter((b) => b.textContent.includes('Upgrade to Team'));
  billingAPI.createCheckout.mockResolvedValue({ data: {} });
  await act(async () => { upgrade.at(-1).click(); });
  expect(billingAPI.createCheckout).toHaveBeenCalledWith('price_team', expect.any(String), expect.any(String));
});
