globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import axe from 'axe-core';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import LoginPage from '../pages/LoginPage';
import RegisterPage from '../pages/RegisterPage';
import ContractDetailPage from '../pages/ContractDetailPage';
import TemplatesPage from '../pages/TemplatesPage';
import TeamPage from '../pages/TeamPage';
import { contractsAPI, versionsAPI, workflowAPI, templatesAPI, teamAPI } from '../api';
import { ThemeProvider } from '../contexts/ThemeContext';

jest.mock('../api', () => ({
  contractsAPI: { get: jest.fn(), chat: jest.fn(), delete: jest.fn() },
  versionsAPI: { getVersions: jest.fn(), getVersion: jest.fn(), restore: jest.fn() },
  workflowAPI: { getHistory: jest.fn(), performAction: jest.fn() },
  templatesAPI: { list: jest.fn(), create: jest.fn(), delete: jest.fn() },
  teamAPI: { listMembers: jest.fn(), listInvitations: jest.fn(), invite: jest.fn(), updateRole: jest.fn(), removeMember: jest.fn(), cancelInvitation: jest.fn() },
  exportAPI: {},
  agenticAPI: {},
}));
jest.mock('./Layout', () => ({ Layout: ({ children }) => <main>{children}</main> }));
const admin = { id: 'u1', firstName: 'Austin', lastName: 'Auger', email: 'austin@example.com', role: 'admin' };
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: admin, login: jest.fn(), register: jest.fn(), logout: jest.fn() }),
}));

let container;
let root;

beforeEach(() => {
  contractsAPI.get.mockResolvedValue({
    data: {
      id: 'c1', title: 'Master Services Agreement', counterparty: 'Acme Corp', contractType: 'service',
      status: 'active', riskLevel: 'low', createdAt: '2026-09-01T00:00:00Z', aiAnalysis: {},
    },
  });
  versionsAPI.getVersions.mockResolvedValue({ data: [] });
  workflowAPI.getHistory.mockResolvedValue({ data: { history: [] } });
  templatesAPI.list.mockResolvedValue({
    data: [
      { id: 't1', name: 'Standard NDA', description: 'Mutual', contractType: 'nda', isPublic: false, tags: [], content: 'NDA' },
      { id: 'default-msa', name: 'Services MSA', description: 'Built in', contractType: 'service', isPublic: true, tags: [], content: 'MSA' },
    ],
  });
  teamAPI.listMembers.mockResolvedValue({
    data: [
      admin,
      { id: 'u2', firstName: 'Jackson', lastName: 'Lee', email: 'jackson@example.com', role: 'manager', isActive: true },
      { id: 'u3', email: 'sam@example.com', role: 'user', isActive: true },
    ],
  });
  teamAPI.listInvitations.mockResolvedValue({
    data: [{ id: 'i1', email: 'new@example.com', role: 'user', status: 'pending', expiresAt: '2026-10-26T00:00:00Z' }],
  });
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
    root.render(
      <ThemeProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/contracts/:id" element={ui} />
            <Route path="*" element={ui} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const RULES = ['button-name', 'link-name', 'label-content-name-mismatch', 'aria-valid-attr-value', 'aria-input-field-name'];
async function axeViolations() {
  const result = await axe.run(container, { runOnly: { type: 'rule', values: RULES } });
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(' | ')}`);
}
const byTestId = (id) => container.querySelector(`[data-testid="${id}"]`);
const label = (id) => byTestId(id)?.getAttribute('aria-label');

describe.each([['Login', LoginPage, 'login-password'], ['Register', RegisterPage, 'register-password']])(
  '%s password toggle',
  (_name, Page, inputId) => {
    test('name follows its state and it points at the password field', async () => {
      await render(<Page />);
      const toggle = byTestId('password-toggle');
      expect(toggle.getAttribute('aria-label')).toBe('Show password');
      expect(toggle.getAttribute('aria-controls')).toBe(byTestId(inputId).id);
      expect(byTestId(inputId).getAttribute('type')).toBe('password');

      await act(async () => { toggle.click(); });
      expect(toggle.getAttribute('aria-label')).toBe('Hide password');
      expect(byTestId(inputId).getAttribute('type')).toBe('text');

      await act(async () => { toggle.click(); });
      expect(toggle.getAttribute('aria-label')).toBe('Show password');
      expect(await axeViolations()).toEqual([]);
    });
  },
);

describe('Contract detail', () => {
  test('back and send buttons are named', async () => {
    await render(<ContractDetailPage />, '/contracts/c1');
    expect(label('back-btn')).toBe('Back to contracts');
    expect(label('chat-send')).toBe('Send question');
    for (const id of ['back-btn', 'chat-send']) {
      expect(byTestId(id).querySelector('svg').getAttribute('aria-hidden')).toBe('true');
    }
    expect(await axeViolations()).toEqual([]);
  });
});

describe('Templates', () => {
  test('card icon buttons name the template', async () => {
    await render(<TemplatesPage />);
    expect(label('copy-template-t1')).toBe('Copy the text of Standard NDA');
    expect(label('delete-template-t1')).toBe('Delete Standard NDA');
    expect(label('copy-template-default-msa')).toBe('Copy the text of Services MSA');
    expect(byTestId('delete-template-default-msa')).toBeNull(); // built-in templates can't be deleted
    expect(await axeViolations()).toEqual([]);
  });
});

describe('Team', () => {
  test('row menus name the member, invite cancel names the email', async () => {
    await render(<TeamPage />);
    expect(byTestId('member-actions-u1')).toBeNull(); // no menu on your own row
    expect(label('member-actions-u2')).toBe('Actions for Jackson Lee');
    expect(label('member-actions-u3')).toBe('Actions for sam@example.com');
    expect(label('cancel-invite-i1')).toBe('Cancel the invite for new@example.com');
    expect(await axeViolations()).toEqual([]);
  });
});
