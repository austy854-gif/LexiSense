import { Component } from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import ContractDetailPage from './ContractDetailPage';

const mockContract = {
  id: 'c1',
  title: 'Sample services agreement',
  counterparty: 'Sample Vendor',
  contractType: 'Service Agreement',
  status: 'draft',
  riskLevel: 'low',
  uploaderEmail: 'owner@example.com',
  aiAnalysisStatus: 'completed',
  aiAnalysis: { summary: 'A short sample summary.', keyTerms: ['Net 30'], risks: [], recommendations: [], parties: [] },
};

jest.mock('../api', () => ({
  contractsAPI: { get: jest.fn(), chat: jest.fn() },
  versionsAPI: { getVersions: jest.fn() },
  workflowAPI: { getHistory: jest.fn(), performAction: jest.fn() },
}));
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', role: 'admin', email: 'owner@example.com' }, loading: false }),
}));
// The app shell (sidebar, notifications) is not under test here.
jest.mock('../components/Layout', () => ({ Layout: ({ children }) => <div>{children}</div> }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// jsdom has no layout APIs used by the chat auto-scroll and Radix ScrollArea.
Element.prototype.scrollIntoView = jest.fn();
globalThis.ResizeObserver = globalThis.ResizeObserver || class { observe() {} unobserve() {} disconnect() {} };

class TestErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  render() {
    if (this.state.error) return <div data-testid="error-boundary">{String(this.state.error)}</div>;
    return this.props.children;
  }
}

// react-scripts runs Jest with resetMocks, so set implementations per test.
beforeEach(() => {
  const { contractsAPI, versionsAPI, workflowAPI } = require('../api');
  contractsAPI.get.mockResolvedValue({ data: mockContract });
  versionsAPI.getVersions.mockResolvedValue({ data: [] });
  workflowAPI.getHistory.mockResolvedValue({ data: { history: [] } });
});

test('contract detail page renders without falling into the error boundary', async () => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});

  await act(async () => {
    root.render(
      <TestErrorBoundary>
        <MemoryRouter initialEntries={['/contracts/c1']}>
          <Routes>
            <Route path="/contracts/:id" element={<ContractDetailPage />} />
          </Routes>
        </MemoryRouter>
      </TestErrorBoundary>,
    );
  });

  expect(container.querySelector('[data-testid="error-boundary"]')).toBeNull();
  expect(container.textContent).toContain('Sample services agreement');
  const { contractsAPI, versionsAPI, workflowAPI } = require('../api');
  expect(contractsAPI.get).toHaveBeenCalledWith('c1');
  expect(versionsAPI.getVersions).toHaveBeenCalledWith('c1');
  expect(workflowAPI.getHistory).toHaveBeenCalledWith('c1');

  consoleError.mockRestore();
  act(() => root.unmount());
  container.remove();
});
