globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { AuthProvider, useAuth } from './AuthContext';
import { authAPI, billingAPI } from '../api';
import { hasCapability } from '../lib/access.mjs';

jest.mock('../api', () => ({
  authAPI: { getMe: jest.fn(), login: jest.fn(), register: jest.fn(), logout: jest.fn() },
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


function deferred() {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const allowed = { data: { state: 'paid', readOnly: false, capabilities: { canUpload: true } } };
const denied = { data: { state: 'paid', readOnly: true, capabilities: { canUpload: false } } };

test.each(['success', 'failure'])('an older refresh %s cannot overwrite a newer request', async (outcome) => {
  billingAPI.getCapabilities.mockResolvedValue(allowed);
  await mount();
  const old = deferred();
  billingAPI.getCapabilities.mockReturnValueOnce(old.promise).mockResolvedValueOnce(allowed);
  let pending;
  act(() => { pending = seen.refreshAccess(); });
  await act(async () => { await seen.refreshAccess(); });
  await act(async () => {
    if (outcome === 'success') old.resolve(denied);
    else old.reject(new Error('old request failed'));
    await pending;
  });
  expect(hasCapability(seen.access, 'canUpload')).toBe(true);
});

test.each([
  ['login', 'success'], ['login', 'failure'],
  ['register', 'success'], ['register', 'failure'],
  ['logout', 'success'], ['logout', 'failure'],
])('an earlier session refresh %s/%s cannot replace current access', async (change, outcome) => {
  billingAPI.getCapabilities.mockResolvedValue(allowed);
  await mount();
  const old = deferred();
  billingAPI.getCapabilities.mockReturnValueOnce(old.promise).mockResolvedValueOnce(allowed);
  let pending;
  act(() => { pending = seen.refreshAccess(); });
  authAPI.login.mockResolvedValue({ data: { access_token: 'new', user: { id: 'b' } } });
  authAPI.register.mockResolvedValue({ data: { access_token: 'new', user: { id: 'b' } } });
  authAPI.logout.mockResolvedValue({});
  await act(async () => { await seen[change]('b@example.com', 'password'); });
  const current = seen.access;
  await act(async () => {
    if (outcome === 'success') old.resolve(change === 'logout' ? allowed : denied);
    else old.reject(new Error('old session request failed'));
    await pending;
  });
  expect(seen.access).toBe(current);
  expect(hasCapability(seen.access, 'canUpload')).toBe(change !== 'logout');
});
