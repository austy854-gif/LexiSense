import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Layout } from './Layout';
import { BillingUiRoute } from './ProtectedRoute';
import { billingAPI } from '../api';
import { resetBillingConfigCache } from '../lib/billingConfig';

jest.mock('../api', () => ({
  billingAPI: { getConfig: jest.fn() },
  notificationsAPI: {
    list: jest.fn(() => Promise.resolve({ data: [] })),
    unreadCount: jest.fn(() => Promise.resolve({ data: { count: 0 } })),
    markAllRead: jest.fn(),
    markRead: jest.fn(),
  },
}));
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { role: 'admin', email: 'a@example.com' }, logout: jest.fn(), loading: false }),
}));
jest.mock('../contexts/ThemeContext', () => ({ useTheme: () => ({ theme: 'light', toggleTheme: jest.fn() }) }));
// CRA's Jest cannot resolve react-router v7's package `exports`; stub what we use.
jest.mock('react-router-dom', () => {
  const React = require('react');
  return {
    Link: ({ to, children, ...props }) => React.createElement('a', { href: to, ...props }, children),
    Navigate: ({ to }) => React.createElement('div', { 'data-testid': 'redirect', 'data-to': to }),
    useLocation: () => ({ pathname: '/' }),
    useNavigate: () => jest.fn(),
  };
});

// Radix primitives use package `exports` subpaths CRA's Jest can't resolve;
// the nav under test doesn't need them, so render plain wrappers.
jest.mock('./ui/avatar', () => ({
  Avatar: ({ children }) => children,
  AvatarFallback: ({ children }) => children,
}));
jest.mock('./ui/scroll-area', () => ({ ScrollArea: ({ children }) => children }));
jest.mock('./ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }) => children,
  DropdownMenuContent: () => null,
  DropdownMenuItem: () => null,
  DropdownMenuSeparator: () => null,
  DropdownMenuTrigger: ({ children }) => children,
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

beforeEach(() => {
  resetBillingConfigCache();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  jest.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.restoreAllMocks();
});

async function render(ui) {
  await act(async () => {
    root.render(ui);
  });
}

const billingLinks = () => container.querySelectorAll('a[href="/billing"]');

test('Billing nav entry is hidden while BILLING_UI_ENABLED is off', async () => {
  billingAPI.getConfig.mockResolvedValue({ data: { billingUiEnabled: false } });
  await render(<Layout><p>page</p></Layout>);
  expect(container.querySelector('a[href="/contracts"]')).not.toBeNull();
  expect(billingLinks()).toHaveLength(0);
});

test('Billing nav entry is hidden if the flag cannot be loaded', async () => {
  billingAPI.getConfig.mockRejectedValue(new Error('Network Error'));
  await render(<Layout><p>page</p></Layout>);
  expect(billingLinks()).toHaveLength(0);
});

test('Billing nav entry is shown when the flag is on', async () => {
  billingAPI.getConfig.mockResolvedValue({ data: { billingUiEnabled: true } });
  await render(<Layout><p>page</p></Layout>);
  expect(billingLinks().length).toBeGreaterThan(0);
});

test('the /billing route redirects home while the flag is off', async () => {
  billingAPI.getConfig.mockResolvedValue({ data: { billingUiEnabled: false } });
  await render(<BillingUiRoute><p data-testid="billing">billing</p></BillingUiRoute>);
  expect(container.querySelector('[data-testid="billing"]')).toBeNull();
  expect(container.querySelector('[data-testid="redirect"]').dataset.to).toBe('/');
});

test('the /billing route renders when the flag is on', async () => {
  billingAPI.getConfig.mockResolvedValue({ data: { billingUiEnabled: true } });
  await render(<BillingUiRoute><p data-testid="billing">billing</p></BillingUiRoute>);
  expect(container.querySelector('[data-testid="billing"]')).not.toBeNull();
});
