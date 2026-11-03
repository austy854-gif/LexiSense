import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { HomeRoute } from './HomeRoute';
import { HostAuthRedirect } from './HostAuthRedirect';

let mockAuth = { user: null, loading: false };
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => mockAuth,
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let container;
let root;
function render(ui) {
  act(() => { root.render(ui); });
}
const text = () => container.textContent;

const ORIGINAL_ENV = process.env;
const originalLocation = window.location;

function setHost(host, pathname = '/') {
  const replace = jest.fn();
  delete window.location;
  window.location = { host, pathname, search: '', hash: '', origin: `https://${host}`, replace };
  return replace;
}

beforeEach(() => {
  mockAuth = { user: null, loading: false };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  process.env = {
    ...ORIGINAL_ENV,
    REACT_APP_APP_URL: 'https://app.lexisense.net',
    REACT_APP_LANDING_URL: 'https://lexisense.net',
  };
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  process.env = ORIGINAL_ENV;
  window.location = originalLocation;
});

const renderHome = () => render(

  <MemoryRouter initialEntries={['/']}>
    <Routes>
      <Route path="/" element={<HomeRoute landing={<p>landing</p>} dashboard={<p>dash</p>} />} />
      <Route path="/login" element={<p>login page</p>} />
    </Routes>
  </MemoryRouter>
);

test('app host: logged-out "/" goes to /login', () => {
  setHost('app.lexisense.net');
  renderHome();
  expect(text()).toContain('login page');
});

test('landing host: logged-out "/" shows the landing page', () => {
  setHost('www.lexisense.net');
  renderHome();
  expect(text()).toBe('landing');
});

test('vars unset: unchanged behaviour', () => {
  process.env = { ...ORIGINAL_ENV, REACT_APP_APP_URL: '', REACT_APP_LANDING_URL: '' };
  setHost('localhost:3000');
  renderHome();
  expect(text()).toBe('landing');
});

test('landing host: /login does a full navigation to the app host', () => {
  const replace = setHost('lexisense.net', '/login');
  render(<HostAuthRedirect><p>login page</p></HostAuthRedirect>);
  expect(replace).toHaveBeenCalledWith('https://app.lexisense.net/login');
  expect(text()).not.toContain('login page');
});

test('app host: /login renders in place', () => {
  const replace = setHost('app.lexisense.net', '/login');
  render(<HostAuthRedirect><p>login page</p></HostAuthRedirect>);
  expect(replace).not.toHaveBeenCalled();
  expect(text()).toContain('login page');
});

test('landing host: a stale signed-in session still sees the landing page', () => {
  mockAuth = { user: { id: 1 }, loading: false };
  setHost('lexisense.net');
  renderHome();
  expect(text()).toBe('landing');
});

test('app host: signed-in user gets the dashboard', () => {
  mockAuth = { user: { id: 1 }, loading: false };
  setHost('app.lexisense.net');
  renderHome();
  expect(text()).toBe('dash');
});

test('landing host: legacy invite link is forwarded to the app host with its token', () => {
  const replace = jest.fn();
  delete window.location;
  window.location = { host: 'lexisense.net', pathname: '/accept-invite', search: '?token=abc', hash: '', origin: 'https://lexisense.net', replace };
  render(<HostAuthRedirect><p>invite</p></HostAuthRedirect>);
  expect(replace).toHaveBeenCalledWith('https://app.lexisense.net/accept-invite?token=abc');
});
