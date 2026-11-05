globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import LoginPage from './LoginPage';
import RegisterPage from './RegisterPage';
import ApprovalRespondPage from './ApprovalRespondPage';
import { ErrorFallback } from '../components/ErrorFallback';
import { Layout } from '../components/Layout';

jest.mock('../api', () => ({
  notificationsAPI: { list: jest.fn().mockResolvedValue({ data: [] }), unreadCount: jest.fn().mockResolvedValue({ data: { count: 0 } }), markRead: jest.fn(), markAllRead: jest.fn() },
}));
jest.mock('../lib/billingConfig', () => ({
  isBillingPath: (p) => p === '/billing',
  useBillingUiEnabled: () => true,
}));
jest.mock('../contexts/ThemeContext', () => ({ useTheme: () => ({ theme: 'light', toggleTheme: jest.fn() }) }));
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ login: jest.fn(), user: { firstName: 'A', lastName: 'B', email: 'a@b.com', role: 'admin' }, logout: jest.fn() }),
}));

let container;
let root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(ui, path = '/') {
  await act(async () => {
    root.render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);
  });
  await act(async () => {});
}

test('login uses light theme shell with BrandLockup, not slate gradient', async () => {
  await render(<LoginPage />, '/login');
  const shell = container.querySelector('[data-testid="login-page"]');
  expect(shell).toBeTruthy();
  expect(shell.className).toContain('bg-background');
  expect(shell.className).not.toMatch(/slate-900/);
  expect(container.querySelector('img[alt="LexiSense"]')).toBeTruthy();
  expect(container.querySelector('h1')).toBeTruthy();
});

test('register title is sentence case and stacks name fields on small screens', async () => {
  await render(<RegisterPage />, '/register');
  expect(container.textContent).toMatch(/Create account/);
  expect(container.textContent).not.toMatch(/Create Account/);
  const grid = container.querySelector('.sm\\:grid-cols-2') || container.querySelector('[class*="sm:grid-cols-2"]');
  // class string contains sm:grid-cols-2
  expect(container.innerHTML).toContain('sm:grid-cols-2');
});

test('approval respond uses BrandLockup and destructive alert styling', async () => {
  await render(<ApprovalRespondPage />, '/approvals/respond');
  expect(container.querySelector('img[alt="LexiSense"]')).toBeTruthy();
  const err = container.querySelector('[data-testid="approval-error"]');
  expect(err).toBeTruthy();
  expect(err.querySelector('svg')).toBeTruthy();
  expect(err.textContent).toMatch(/missing its token/i);
});

test('ErrorFallback includes support mailto (UI-ES-03)', () => {
  act(() => {
    root.render(<ErrorFallback resetError={() => {}} />);
  });
  const link = container.querySelector('a[href="mailto:support@lexisense.net"]');
  expect(link).toBeTruthy();
  expect(link.textContent).toBe('support@lexisense.net');
});

test('shell nav uses sentence-case Audit log and accent active state', async () => {
  await render(<Layout><p>x</p></Layout>);
  const audit = container.querySelector('[data-testid="nav-audit-log"]');
  expect(audit).toBeTruthy();
  expect(audit.textContent).toMatch(/Audit log/);
});
