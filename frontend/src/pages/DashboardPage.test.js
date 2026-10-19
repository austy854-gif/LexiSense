import { act } from 'react';
import { createRoot } from 'react-dom/client';
import DashboardPage from './DashboardPage';
import { dashboardAPI } from '../api';

jest.mock('../api', () => ({ dashboardAPI: { getStats: jest.fn() } }));
// CRA's Jest cannot resolve react-router v7's package `exports`, and the page
// only needs <Link> to render, so stub it with a plain anchor.
jest.mock('react-router-dom', () => ({
  Link: ({ to, children, ...props }) => require('react').createElement('a', { href: to, ...props }, children),
}));
jest.mock('../components/Layout', () => ({ Layout: ({ children }) => children }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  jest.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.restoreAllMocks();
  dashboardAPI.getStats.mockReset();
});

async function render() {
  await act(async () => {
    root.render(<DashboardPage />);
  });
}

test('a failed load shows an error with retry, not an empty account', async () => {
  dashboardAPI.getStats.mockRejectedValueOnce(new Error('Network Error'));
  await render();

  expect(container.querySelector('[data-testid="dashboard-load-error"]')).not.toBeNull();
  expect(container.textContent).toContain("We couldn't load your dashboard.");
  expect(container.textContent).not.toContain('No contracts yet');

  dashboardAPI.getStats.mockResolvedValueOnce({ data: { overview: { totalContracts: 37 }, recentContracts: [] } });
  await act(async () => {
    container.querySelector('[data-testid="dashboard-retry"]').click();
  });

  expect(dashboardAPI.getStats).toHaveBeenCalledTimes(2);
  expect(container.querySelector('[data-testid="dashboard-load-error"]')).toBeNull();
  expect(container.textContent).toContain('37');
});

test('a successful empty load still shows the empty state', async () => {
  dashboardAPI.getStats.mockResolvedValueOnce({ data: { overview: {}, recentContracts: [] } });
  await render();
  expect(container.querySelector('[data-testid="dashboard-load-error"]')).toBeNull();
  expect(container.textContent).toContain('No contracts yet');
});
