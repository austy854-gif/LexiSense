import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ProtectedRoute } from './ProtectedRoute';

let mockAuth = { user: null, loading: false };
jest.mock('../contexts/AuthContext', () => ({ useAuth: () => mockAuth }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let container;
let root;
const ORIGINAL_ENV = process.env;
const originalLocation = window.location;

function setLocation(host, pathname, search = '', hash = '') {
  const replace = jest.fn();
  delete window.location;
  window.location = { host, pathname, search, hash, origin: `https://${host}`, replace };
  return replace;
}

function renderAt(path, roles) {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/contracts" element={<ProtectedRoute allowedRoles={roles}><p>contracts</p></ProtectedRoute>} />
          <Route path="/login" element={<p>login page</p>} />
          <Route path="/" element={<p>home</p>} />
        </Routes>
      </MemoryRouter>
    );
  });
}
const text = () => container.textContent;

beforeEach(() => {
  mockAuth = { user: null, loading: false };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  process.env = { ...ORIGINAL_ENV, REACT_APP_APP_URL: 'https://app.lexisense.net', REACT_APP_LANDING_URL: 'https://lexisense.net', REACT_APP_AUTH_ON_LANDING: 'false' };
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  process.env = ORIGINAL_ENV;
  window.location = originalLocation;
});

test('landing host: redirects to app host preserving path, query and hash, even while loading', () => {
  mockAuth = { user: null, loading: true };
  const replace = setLocation('www.lexisense.net', '/contracts', '?q=1', '#top');
  renderAt('/contracts');
  expect(replace).toHaveBeenCalledWith('https://app.lexisense.net/contracts?q=1#top');
  expect(text()).not.toContain('login page');
});

test('landing host: stale session does not render protected content', () => {
  mockAuth = { user: { id: 1, role: 'admin' }, loading: false };
  const replace = setLocation('lexisense.net', '/contracts');
  renderAt('/contracts');
  expect(replace).toHaveBeenCalledWith('https://app.lexisense.net/contracts');
  expect(text()).not.toContain('contracts');
});

test('app host: logged out goes to /login (unchanged)', () => {
  const replace = setLocation('app.lexisense.net', '/contracts');
  renderAt('/contracts');
  expect(replace).not.toHaveBeenCalled();
  expect(text()).toBe('login page');
});

test('app host: signed in renders children; role check unchanged', () => {
  mockAuth = { user: { id: 1, role: 'viewer' }, loading: false };
  setLocation('app.lexisense.net', '/contracts');
  renderAt('/contracts');
  expect(text()).toBe('contracts');
  act(() => root.unmount());
  root = createRoot(container);
  renderAt('/contracts', ['admin']);
  expect(text()).toBe('home');
});

test('vars unset: unchanged flow', () => {
  process.env = { ...ORIGINAL_ENV, REACT_APP_APP_URL: '', REACT_APP_LANDING_URL: '' };
  const replace = setLocation('localhost:3000', '/contracts');
  renderAt('/contracts');
  expect(replace).not.toHaveBeenCalled();
  expect(text()).toBe('login page');
});

test('temp auth-on-landing: protected route stays on landing and applies auth checks', () => {
  process.env = { ...ORIGINAL_ENV, REACT_APP_APP_URL: 'https://app.lexisense.net', REACT_APP_LANDING_URL: 'https://lexisense.net' };
  delete process.env.REACT_APP_AUTH_ON_LANDING;
  const replace = setLocation('www.lexisense.net', '/contracts');
  renderAt('/contracts');
  expect(replace).not.toHaveBeenCalled();
  expect(text()).toBe('login page');
});
