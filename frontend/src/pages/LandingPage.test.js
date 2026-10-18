import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { HomeRoute } from '../components/HomeRoute';
import LandingPage from './LandingPage';

let mockAuth = { user: null, loading: false };
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => mockAuth,
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

function render(ui, { path = '/' } = {}) {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={ui} />
          <Route path="/register" element={<div data-testid="register-page">register</div>} />
          <Route path="/login" element={<div data-testid="login-page">login</div>} />
        </Routes>
      </MemoryRouter>,
    );
  });
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  mockAuth = { user: null, loading: false };
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const home = () => (
  <HomeRoute landing={<LandingPage />} dashboard={<div data-testid="dashboard">dashboard</div>} />
);

test('logged-out visitors see the landing page at /', () => {
  render(home());
  expect(container.querySelector('.lexi-landing')).not.toBeNull();
  expect(container.querySelector('[data-testid="dashboard"]')).toBeNull();
  expect(container.querySelector('h1').textContent).toMatch(/See every renewal coming/);
});

test('signed-in users get the dashboard at /, not the landing page', () => {
  mockAuth = { user: { id: 'u1', role: 'admin' }, loading: false };
  render(home());
  expect(container.querySelector('[data-testid="dashboard"]')).not.toBeNull();
  expect(container.querySelector('.lexi-landing')).toBeNull();
});

test('every "Try LexiSense free" CTA points to /register', () => {
  render(home());
  const ctas = [...container.querySelectorAll('a')].filter((a) => a.textContent.trim() === 'Try LexiSense free');
  expect(ctas.length).toBeGreaterThanOrEqual(3);
  ctas.forEach((a) => expect(a.getAttribute('href')).toBe('/register'));
});

test('mobile menu toggle: aria-expanded, Escape and link clicks close it', () => {
  render(home());
  const toggle = container.querySelector('button[aria-controls="landing-mobile-menu"]');
  const menu = container.querySelector('#landing-mobile-menu');
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(menu.hidden).toBe(true);

  act(() => toggle.click());
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  expect(toggle.getAttribute('aria-label')).toBe('Close menu');
  expect(menu.hidden).toBe(false);

  // Escape closes and returns focus to the toggle.
  act(() => {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  });
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(menu.hidden).toBe(true);
  expect(document.activeElement).toBe(toggle);

  // Choosing a section link closes the menu.
  act(() => toggle.click());
  const faqLink = [...menu.querySelectorAll('a')].find((a) => a.getAttribute('href') === '#faq');
  act(() => faqLink.click());
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(menu.hidden).toBe(true);
});

test('risk is described per contract, never per clause', () => {
  render(home());
  expect(container.textContent).not.toMatch(/clauses? worth/i);
  expect(container.textContent).toMatch(/flags higher-risk contracts/);
});

test('landing copy never claims OCR or scanned-document support', () => {
  render(home());
  const text = container.textContent;
  expect(text).not.toMatch(/\bOCR\b/i);
  // The only mention of scans allowed is the FAQ saying they aren't supported.
  const scanSentences = text.match(/[^.?]*\bscan\w*[^.]*\./gi) || [];
  scanSentences.forEach((sentence) => expect(sentence).toMatch(/(n[’']t|not) (supported|be read)/));
});
