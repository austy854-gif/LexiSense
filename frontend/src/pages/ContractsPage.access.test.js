globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ContractsPage from './ContractsPage';
import { contractsAPI } from '../api';
import { FAILED_ACCESS, LOADING_ACCESS } from '../lib/access.mjs';

let mockAccess;
jest.mock('../api', () => ({
  notificationsAPI: { list: jest.fn(), unreadCount: jest.fn(), markRead: jest.fn(), markAllRead: jest.fn() },
  contractsAPI: { list: jest.fn(), upload: jest.fn(), delete: jest.fn(), bulkUpload: jest.fn() },
  exportAPI: {},
}));
jest.mock('../components/Layout', () => ({ Layout: ({ children }) => <main>{children}</main> }));
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'a', email: 'a@example.com', role: 'admin' }, access: mockAccess, logout: jest.fn() }),
}));

const { accessForRole } = require('../testUtils/accessFixture');

let container;
let root;

beforeEach(() => {
  contractsAPI.list.mockResolvedValue({
    data: [{ id: 'c1', title: 'MSA', status: 'active', contractType: 'service', createdAt: '2026-09-01T00:00:00Z' }],
    headers: { 'x-total-count': '1' },
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function renderWith(access) {
  mockAccess = access;
  await act(async () => { root.render(<MemoryRouter><ContractsPage /></MemoryRouter>); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const has = (id) => container.querySelector(`[data-testid="${id}"]`) !== null;

test.each([
  ['loading', LOADING_ACCESS],
  ['failed', FAILED_ACCESS],
  ['read-only (past due)', accessForRole('admin', { readOnly: true })],
])('admin sees no upload or delete actions when access is %s', async (_label, access) => {
  await renderWith(access);
  expect(has('upload-contract-btn')).toBe(false);
  expect(has('bulk-upload-btn')).toBe(false);
  expect(has('delete-c1')).toBe(false);
});

test('admin with loaded writable access sees upload and delete', async () => {
  await renderWith(accessForRole('admin'));
  expect(has('upload-contract-btn')).toBe(true);
  expect(has('delete-c1')).toBe(true);
});

test('a user can upload but not delete', async () => {
  await renderWith(accessForRole('user'));
  expect(has('upload-contract-btn')).toBe(true);
  expect(has('delete-c1')).toBe(false);
});
