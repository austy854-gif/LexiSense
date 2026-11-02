globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import AlertsPage from './AlertsPage';
import { alertsAPI } from '../api';
import { FAILED_ACCESS } from '../lib/access.mjs';

let mockAccess;
jest.mock('../api', () => ({
  alertsAPI: {
    getSettings: jest.fn(), updateSettings: jest.fn(), getExpiring: jest.fn(),
    checkAndSend: jest.fn(), getHistory: jest.fn(),
  },
}));
jest.mock('../components/Layout', () => ({ Layout: ({ children }) => <main>{children}</main> }));
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'a', role: 'admin' }, access: mockAccess }),
}));

const { accessForRole } = require('../testUtils/accessFixture');

let container;
let root;

beforeEach(() => {
  for (const fn of Object.values(alertsAPI)) fn.mockResolvedValue({ data: {} });
  alertsAPI.getSettings.mockResolvedValue({ data: { emailEnabled: true, alertDays: [30, 7] } });
  alertsAPI.getExpiring.mockResolvedValue({ data: { contracts: [] } });
  alertsAPI.getHistory.mockResolvedValue({ data: { alerts: [] } });
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
  await act(async () => { root.render(<MemoryRouter><AlertsPage /></MemoryRouter>); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const hasSend = () => container.querySelector('[data-testid="send-alerts-btn"]') !== null;

test('renders for an admin with loaded access', async () => {
  await renderWith(accessForRole('admin'));
  expect(container.textContent.length).toBeGreaterThan(0);
  expect(hasSend()).toBe(true);
});

test('renders without write actions when access failed', async () => {
  await renderWith(FAILED_ACCESS);
  expect(container.textContent.length).toBeGreaterThan(0);
  expect(hasSend()).toBe(false);
});
