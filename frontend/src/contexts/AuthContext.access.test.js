globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { AuthProvider, useAuth } from './AuthContext';
import { authAPI, billingAPI } from '../api';
import { hasCapability } from '../lib/access.mjs';

jest.mock('../api', () => ({
  authAPI: { getMe: jest.fn(), login: jest.fn(), register: jest.fn() },
  billingAPI: { getCapabilities: jest.fn() },
}));

let container;
let root;
let seen;

function Probe() {
  seen = useAuth();
  return null;
}

beforeEach(() => {
  localStorage.setItem('token', 't');
  localStorage.setItem('user', JSON.stringify({ id: 'a', role: 'admin' }));
  authAPI.getMe.mockResolvedValue({ data: { id: 'a', role: 'admin' } });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  localStorage.clear();
  jest.resetAllMocks();
});

async function mount() {
  await act(async () => { root.render(<AuthProvider><Probe /></AuthProvider>); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

test('an admin gets no write capabilities when /billing/capabilities fails', async () => {
  billingAPI.getCapabilities.mockRejectedValue(new Error('billing down'));
  await mount();
  expect(seen.user.role).toBe('admin');
  expect(seen.access.status).toBe('error');
  expect(seen.access.readOnly).toBe(true);
  for (const key of ['canUpload', 'canDelete', 'canApprove', 'canManageTemplates', 'canTriggerAlerts']) {
    expect(hasCapability(seen.access, key)).toBe(false);
  }
});

test('capabilities are closed before the first fetch resolves', async () => {
  let resolve;
  billingAPI.getCapabilities.mockReturnValue(new Promise((r) => { resolve = r; }));
  await mount();
  expect(seen.access.ready).toBe(false);
  expect(hasCapability(seen.access, 'canUpload')).toBe(false);
  await act(async () => {
    resolve({ data: { state: 'paid', readOnly: false, capabilities: { canUpload: true } } });
    await new Promise((r) => setTimeout(r, 0));
  });
  expect(hasCapability(seen.access, 'canUpload')).toBe(true);
});

test('an empty capabilities payload grants nothing', async () => {
  billingAPI.getCapabilities.mockResolvedValue({ data: { state: 'paid', readOnly: false, capabilities: {} } });
  await mount();
  expect(seen.access.ready).toBe(true);
  expect(hasCapability(seen.access, 'canUpload')).toBe(false);
});
