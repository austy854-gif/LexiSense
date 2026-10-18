import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import {
  NoContractsIllustration,
  NoSearchResultsIllustration,
  NothingExpiringIllustration,
  SomethingWentWrongIllustration,
  UploadFailedIllustration,
} from './illustrations/Illustrations';
import { ErrorFallback } from './ErrorFallback';
import { ContractsEmptyState, uploadErrorDetails } from '../pages/ContractsPage';

jest.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ user: null, loading: false }) }));
jest.mock('../api', () => ({ contractsAPI: {}, exportAPI: {} }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;
const render = (ui) => act(() => root.render(<MemoryRouter>{ui}</MemoryRouter>));

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const ALL = [
  NoContractsIllustration,
  NoSearchResultsIllustration,
  NothingExpiringIllustration,
  SomethingWentWrongIllustration,
  UploadFailedIllustration,
];

describe('illustrations', () => {
  test.each(ALL.map((C) => [C.defaultTitle, C]))('%s is decorative by default', (_, Illustration) => {
    render(<Illustration />);
    const svg = container.querySelector('svg');
    expect(svg.getAttribute('aria-hidden')).toBe('true');
    expect(svg.getAttribute('role')).toBeNull();
    expect(svg.querySelector('title')).toBeNull();
    // Themed via inline styles / CSS variables: no page-wide <style> block.
    expect(container.querySelector('style')).toBeNull();
    expect(svg.innerHTML).toContain('var(--ls-illo-line');
    // The page/paper shape is filled (solid), not transparent: Jackson's
    // `.l:not(.p)` fix means stroke-and-paper shapes keep the paper fill.
    const filled = [...svg.querySelectorAll('[style]')].filter((el) => el.style.fill.includes('--ls-illo-paper'));
    expect(filled.length).toBeGreaterThan(0);
    filled.forEach((el) => expect(el.style.stroke).toContain('--ls-illo-line'));
  });

  test('standalone use gets role="img" and a unique accessible name', () => {
    render(
      <>
        <NoContractsIllustration title={NoContractsIllustration.defaultTitle} description="An empty page" />
        <NoContractsIllustration title="Second copy" />
      </>,
    );
    const [a, b] = container.querySelectorAll('svg');
    expect(a.getAttribute('role')).toBe('img');
    const [titleId, descId] = a.getAttribute('aria-labelledby').split(' ');
    expect(document.getElementById(titleId).textContent).toBe('No contracts yet');
    expect(document.getElementById(descId).textContent).toBe('An empty page');
    expect(b.getAttribute('aria-labelledby')).not.toBe(titleId);
    expect(document.getElementById(b.getAttribute('aria-labelledby')).textContent).toBe('Second copy');
  });
});

describe('ContractsEmptyState', () => {
  const base = { statusFilter: '', typeFilter: '', riskFilter: '', expiringFilter: '' };
  const renderState = (props) =>
    render(<ContractsEmptyState searchQuery="" filters={base} canWrite onUpload={() => {}} onClearFilters={() => {}} {...props} />);

  test('first run: no contracts yet, with upload button for writers', () => {
    renderState();
    expect(container.querySelector('[data-testid="contracts-empty"] h3').textContent).toBe('No contracts yet');
    expect(container.textContent).toContain('Upload a contract');
  });

  test('first run for read-only roles has no upload button', () => {
    renderState({ canWrite: false });
    expect(container.querySelector('button')).toBeNull();
  });

  test('search with no results quotes the query and offers Clear filters', () => {
    const onClearFilters = jest.fn();
    renderState({ searchQuery: 'acme', onClearFilters });
    expect(container.textContent).toContain('No matching contracts');
    expect(container.textContent).toContain(
      'Nothing matches \u201cacme\u201d. Search looks at contract titles and counterparties, so try a different word or clear your filters.',
    );
    act(() => container.querySelector('button').click());
    expect(onClearFilters).toHaveBeenCalled();
  });

  test('an Advanced-only filter (risk) is treated as filtered, not first run', () => {
    renderState({ filters: { ...base, riskFilter: 'high' } });
    expect(container.querySelector('[data-testid="contracts-no-results"]')).not.toBeNull();
    expect(container.textContent).not.toContain('Nothing matches');
  });

  test('filters only (no search term): filter-specific body, no search advice', () => {
    renderState({ filters: { ...base, statusFilter: 'active' } });
    expect(container.querySelector('h3').textContent).toBe('No matching contracts');
    expect(container.textContent).toContain(
      'No contracts match these filters. Try changing them, or clear them to see all your contracts.',
    );
    expect(container.textContent).not.toContain('Search looks at');
    expect(container.querySelector('button').textContent).toBe('Clear filters');
  });

  test('expiring-within filter with nothing in the window shows nothing-expiring', () => {
    renderState({ filters: { ...base, expiringFilter: '60' } });
    expect(container.textContent).toContain('Nothing expiring soon');
    expect(container.textContent).toContain('No contracts expire in the next 60 days.');
  });
});

describe('uploadErrorDetails', () => {
  test('shows the API message for client errors (e.g. a size limit)', () => {
    const error = { response: { status: 413, data: { detail: 'File too large (max 10 MB)' } } };
    expect(uploadErrorDetails(error).apiMessage).toBe('File too large (max 10 MB)');
  });
  test('invents nothing for network or server errors', () => {
    expect(uploadErrorDetails({ message: 'Network Error' }).apiMessage).toBeNull();
    expect(uploadErrorDetails({ response: { status: 500, data: { detail: 'boom' } } }).apiMessage).toBeNull();
  });
});

describe('ErrorFallback', () => {
  test('shows the error state, retries, and has no support email', () => {
    const resetError = jest.fn();
    render(<ErrorFallback resetError={resetError} />);
    expect(container.querySelector('h1').textContent).toBe('Something went wrong');
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.textContent).not.toMatch(/support@|email/i);
    act(() => container.querySelector('button').click());
    expect(resetError).toHaveBeenCalled();
  });
});
