// Approve / Reject / Activate must only render for roles the server permits
// (backend/routes/workflow.py: approve, reject, activate = admin + manager).
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import ContractDetailPage from './ContractDetailPage';
import { contractsAPI } from '../api';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock('../components/Layout', () => ({ Layout: ({ children }) => <div>{children}</div> }));

let mockRole = 'user';
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'u@example.com', role: mockRole } }),
}));
jest.mock('../api', () => ({
  __esModule: true,
  default: {},
  contractsAPI: { get: jest.fn(), chat: jest.fn() },
  versionsAPI: { getVersions: jest.fn(() => Promise.resolve({ data: { versions: [] } })), restore: jest.fn() },
  workflowAPI: { getHistory: jest.fn(() => Promise.resolve({ data: { history: [] } })), performAction: jest.fn() },
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// Radix/scroll-area helpers used by the page expect these in the browser.
global.ResizeObserver = global.ResizeObserver || class { observe() {} unobserve() {} disconnect() {} };
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView || function () {};

let container;
let root;

async function renderAs(role, status) {
  mockRole = role;
  contractsAPI.get.mockResolvedValue({
    data: {
      id: 'c1', title: 'MSA', counterparty: 'Acme', contractType: 'MSA', status,
      uploadedBy: 'u1', aiAnalysisStatus: 'success', tags: [], createdAt: '2026-01-01T00:00:00Z',
    },
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/contracts/c1']}>
        <Routes>
          <Route path="/contracts/:id" element={<ContractDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const has = (id) => container.querySelector(`[data-testid="${id}"]`) !== null;

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

test.each([
  ['viewer', false],
  ['user', false],
  ['manager', true],
  ['admin', true],
])('contract in review: %s sees Approve/Reject = %s', async (role, visible) => {
  await renderAs(role, 'review');
  expect(has('workflow-actions')).toBe(true);
  expect(has('workflow-approve')).toBe(visible);
  expect(has('workflow-reject')).toBe(visible);
});

test.each([
  ['user', false],
  ['manager', true],
])('approved contract: %s sees Activate = %s', async (role, visible) => {
  await renderAs(role, 'approved');
  expect(has('workflow-activate')).toBe(visible);
});

test.each([
  ['viewer', false],
  ['user', true],
])('draft contract: %s sees Submit for review = %s', async (role, visible) => {
  await renderAs(role, 'draft');
  expect(has('workflow-submit')).toBe(visible);
});
