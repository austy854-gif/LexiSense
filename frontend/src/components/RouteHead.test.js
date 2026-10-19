import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { RouteHead } from './RouteHead';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const canonical = () => document.head.querySelector('link[rel="canonical"]');
const robots = () => document.head.querySelector('meta[name="robots"]');

let container;
let root;
let navigate;

function Nav() {
  navigate = useNavigate();
  return null;
}

function renderAt(path) {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <RouteHead />
        <Nav />
      </MemoryRouter>,
    );
  });
}

beforeEach(() => {
  document.head.innerHTML = '';
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

test('"/" gets the canonical link and no robots noindex', () => {
  renderAt('/');
  expect(canonical().getAttribute('href')).toBe('https://lexisense.net/');
  expect(robots()).toBeNull();
});

test.each([
  '/login',
  '/register',
  '/approvals/respond',
  '/contracts',
  '/contracts/abc123',
  '/templates',
  '/analytics',
  '/alerts',
  '/team',
  '/audit',
  '/billing',
])('%s is noindex with no canonical', (path) => {
  renderAt(path);
  expect(robots().getAttribute('content')).toBe('noindex');
  expect(canonical()).toBeNull();
  expect(document.head.querySelectorAll('meta[name="robots"]')).toHaveLength(1);
});

test('client-side navigation swaps the tags both ways', () => {
  renderAt('/');
  act(() => navigate('/login'));
  expect(canonical()).toBeNull();
  expect(robots().getAttribute('content')).toBe('noindex');
  act(() => navigate('/'));
  expect(canonical().getAttribute('href')).toBe('https://lexisense.net/');
  expect(robots()).toBeNull();
});
