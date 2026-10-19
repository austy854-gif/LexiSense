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

let container;
let root;

async function renderWith(subscription) {
  billingAPI.getSubscription.mockResolvedValue({ data: subscription });
  billingAPI.getPlans.mockResolvedValue({ data: { plans: [] } });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(<BillingPage />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return container.querySelector('[data-testid="trial-status"]')?.textContent;
}

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const trial = (trialEndDate) => ({ subscriptionTier: 'trial', billingStatus: 'trialing', trialEndDate });

test('shows "Trial ends {date}" using the backend trial end date', async () => {
  const end = new Date(Date.now() + 5 * 864e5);
  const expected = `Trial ends ${end.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })}`;
  expect(await renderWith(trial(end.toISOString()))).toBe(expected);
  expect(container.textContent).not.toMatch(/\d+\s*days?/i);
});

test('hides the date when the backend has no trial end date', async () => {
  const text = await renderWith(trial(null));
  expect(text).toBe('Free trial ended');
  expect(container.textContent).not.toContain('Trial ends');
});

test('hides the date when it is unparseable', async () => {
  await renderWith(trial('not-a-date'));
  expect(container.textContent).not.toContain('Trial ends');
});
