import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import ContractDetailPage from './ContractDetailPage';

// LX-ENG-KT-01: AI output that drifts from the expected shape must not crash
// the page (or the app). Each analysis panel renders defensively and sits in
// its own error boundary.

jest.mock('../api', () => ({
  contractsAPI: { get: jest.fn(), chat: jest.fn() },
  versionsAPI: { getVersions: jest.fn() },
  workflowAPI: { getHistory: jest.fn(), performAction: jest.fn() },
}));
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', role: 'admin', email: 'owner@example.com' }, loading: false }),
}));
jest.mock('../components/Layout', () => ({ Layout: ({ children }) => <div>{children}</div> }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock('@sentry/react', () => ({ captureException: jest.fn() }));
// Lets one test make a child *inside* the Key Terms boundary throw.
let mockShieldThrows = false;
jest.mock('lucide-react', () => {
  const actual = jest.requireActual('lucide-react');
  return {
    ...actual,
    Shield: (props) => {
      if (mockShieldThrows) throw new Error('key term icon exploded');
      return <actual.Shield {...props} />;
    },
  };
});

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
Element.prototype.scrollIntoView = jest.fn();
globalThis.ResizeObserver = globalThis.ResizeObserver || class { observe() {} unobserve() {} disconnect() {} };

const baseContract = {
  id: 'c1',
  title: 'Drifted analysis agreement',
  contractType: 'Service Agreement',
  status: 'draft',
  uploaderEmail: 'owner@example.com',
  aiAnalysisStatus: 'completed',
};

let container;
let root;
let consoleError;

beforeEach(() => {
  const { versionsAPI, workflowAPI } = require('../api');
  versionsAPI.getVersions.mockResolvedValue({ data: [] });
  workflowAPI.getHistory.mockResolvedValue({ data: { history: [] } });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  mockShieldThrows = false;
  act(() => root.unmount());
  container.remove();
  consoleError.mockRestore();
});

/** Render the detail page for a contract with the given overrides. */
async function renderWith(contract) {
  const { contractsAPI } = require('../api');
  contractsAPI.get.mockResolvedValue({ data: { ...baseContract, ...contract } });
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/contracts/c1']}>
        <Routes>
          <Route path="/contracts/:id" element={<ContractDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
  });
}

/** Activate a Radix tab by its visible label. */
function openTab(label) {
  const trigger = [...container.querySelectorAll('[role="tab"]')].find((el) => el.textContent === label);
  expect(trigger).toBeDefined();
  act(() => {
    trigger.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }));
  });
}

test('object-shaped key terms render as text instead of crashing', async () => {
  await renderWith({
    aiAnalysis: {
      summary: 'Summary.',
      keyTerms: [
        { label: 'Payment', value: 'Net 30' },
        { term: 'Renewal', definition: 'Auto-renews yearly' },
        { name: 'Cap', value: 50000 },
      ],
    },
  });
  openTab('Key Terms');
  const items = [...container.querySelectorAll('[data-testid="key-term"]')].map((li) => li.textContent);
  expect(items).toEqual(['Payment: Net 30', 'Renewal: Auto-renews yearly', 'Cap: 50000']);
  expect(container.querySelector('[data-testid="section-error-fallback"]')).toBeNull();
});

test('mixed and malformed key terms render without throwing', async () => {
  await renderWith({
    aiAnalysis: {
      summary: 'Summary.',
      keyTerms: ['Net 30', 12, null, { value: ['a', { name: 'b' }] }, { weird: { deep: true } }, {}, '<b>not html</b>'],
    },
  });
  openTab('Key Terms');
  const items = [...container.querySelectorAll('[data-testid="key-term"]')].map((li) => li.textContent);
  expect(items).toEqual(['Net 30', '12', 'a, b', 'weird: deep: Yes', '<b>not html</b>']);
  // Rendered as text, never as markup.
  expect(container.querySelector('[data-testid="key-term"] b')).toBeNull();
});

test('key terms as a single object (not a list) still render', async () => {
  await renderWith({ aiAnalysis: { summary: 'S', keyTerms: { label: 'Term', value: '12 months' } } });
  openTab('Key Terms');
  expect(container.querySelector('[data-testid="key-term"]').textContent).toBe('Term: 12 months');
});

test('object-shaped summary, parties, risks and recommendations render safely', async () => {
  await renderWith({
    riskLevel: { level: 'high' },
    counterparty: { name: 'Acme' },
    value: { amount: 100, currency: 'USD' },
    aiAnalysis: {
      summary: { text: 'Object summary' },
      parties: [{ name: 'Acme' }, 'Beta LLC'],
      risks: [
        { risk: { label: 'Liability', value: 'uncapped' }, severity: 'high', recommendation: { text: 'Add a cap' } },
        'Plain risk',
      ],
      recommendations: [{ text: 'Negotiate' }, 3],
    },
  });
  expect(container.textContent).toContain('Object summary');
  expect(container.textContent).toContain('Acme');
  expect(container.textContent).toContain('Beta LLC');
  expect(container.textContent).toContain('amount: 100; currency: USD');

  openTab('Risks');
  expect(container.textContent).toContain('Liability: uncapped');
  expect(container.textContent).toContain('Add a cap');
  expect(container.textContent).toContain('Plain risk');

  // Unknown severity: "Unknown" badge and a neutral icon, never the green "low" one.
  const plainRiskIcon = [...container.querySelectorAll('svg')].find((svg) =>
    svg.closest('div.p-4')?.textContent.includes('Plain risk'),
  );
  expect(plainRiskIcon.getAttribute('class')).toContain('text-muted-foreground');
  expect(plainRiskIcon.getAttribute('class')).not.toContain('text-green-500');

  openTab('Recommendations');
  expect(container.textContent).toContain('Negotiate');
  expect(container.textContent).toContain('3');
  expect(container.querySelector('[data-testid="section-error-fallback"]')).toBeNull();
});

test('a render error inside the Key Terms panel is contained to that panel', async () => {
  mockShieldThrows = true;
  await renderWith({ aiAnalysis: { summary: 'Still readable summary', keyTerms: ['Net 30'], recommendations: ['Do X'] } });
  openTab('Key Terms');
  const fallback = container.querySelector('[data-testid="section-error-fallback"]');
  expect(fallback).not.toBeNull();
  expect(container.textContent).not.toContain('key term icon exploded');
  // Page header and the other tabs still work.
  expect(container.querySelector('h1').textContent).toBe('Drifted analysis agreement');
  expect(container.querySelectorAll('[role="tab"]').length).toBe(6);
  openTab('Recommendations');
  expect(container.textContent).toContain('Do X');
  const Sentry = require('@sentry/react');
  expect(Sentry.captureException).toHaveBeenCalledWith(
    expect.any(Error),
    expect.objectContaining({ tags: { boundary: 'analysis-terms' } }),
  );
});

test('non-array analysis lists do not crash the page', async () => {
  await renderWith({ aiAnalysis: { summary: 'S', risks: 'one risk', recommendations: { text: 'r' }, parties: 'P' } });
  openTab('Risks');
  expect(container.textContent).toContain('one risk');
  openTab('Recommendations');
  expect(container.textContent).toContain('r');
});
