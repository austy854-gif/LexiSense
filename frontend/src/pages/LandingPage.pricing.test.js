import { act } from 'react';
import { createRoot } from 'react-dom/client';
import LandingPage from './LandingPage';
import { PAID_PLANS } from '../lib/plans';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<LandingPage />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const section = () => container.querySelector('#pricing');
const toggle = (label) =>
  [...section().querySelectorAll('[role="group"] button')].find((b) => b.textContent === label);

test('plan constants mirror backend plans.py and approved prices', () => {
  expect(PAID_PLANS.map((p) => [p.name, p.maxMembers, p.prices.monthly, p.prices.annual])).toEqual([
    ['Starter', 5, 49, 39],
    ['Team', 15, 129, 99],
    ['Business', 30, 249, 199],
  ]);
});

test('renders a labelled pricing section with three plan cards as a list', () => {
  const heading = section().querySelector('h2');
  expect(section().getAttribute('aria-labelledby')).toBe(heading.id);
  const cards = section().querySelectorAll('ul[aria-label="Plans"] > li');
  expect(cards).toHaveLength(3);
  expect([...section().querySelectorAll('h3')].map((h) => h.textContent)).toEqual(['Starter', 'Team', 'Business']);
});

test('shows monthly prices and seats by default', () => {
  expect(toggle('Monthly').getAttribute('aria-pressed')).toBe('true');
  expect(toggle('Annual').getAttribute('aria-pressed')).toBe('false');
  for (const [id, price, seats] of [['starter', 49, 5], ['team', 129, 15], ['business', 249, 30]]) {
    expect(container.querySelector(`[data-testid="landing-price-${id}"]`).textContent).toBe(`$${price}`);
    expect(container.querySelector(`[data-testid="landing-plan-${id}"]`).textContent).toContain(`Up to ${seats} members`);
  }
});

test('annual toggle switches to annual per-month prices', () => {
  act(() => toggle('Annual').click());
  expect(toggle('Annual').getAttribute('aria-pressed')).toBe('true');
  expect(toggle('Monthly').getAttribute('aria-pressed')).toBe('false');
  expect(['starter', 'team', 'business'].map((id) => container.querySelector(`[data-testid="landing-price-${id}"]`).textContent)).toEqual(['$39', '$99', '$199']);
  expect(section().textContent).toContain('Billed annually');
  act(() => toggle('Monthly').click());
  expect(container.querySelector('[data-testid="landing-price-team"]').textContent).toBe('$129');
});

test('plan CTAs link to signup', () => {
  const ctas = [...section().querySelectorAll('a')].filter((a) => a.textContent.startsWith('Choose '));
  expect(ctas.map((a) => a.textContent)).toEqual(['Choose Starter', 'Choose Team', 'Choose Business']);
  ctas.forEach((a) => expect(a.getAttribute('href')).toMatch(/\/register$/));
});

test('landing page never claims unlimited anything', () => {
  expect(container.textContent).not.toMatch(/unlimited/i);
  act(() => toggle('Annual').click());
  expect(container.textContent).not.toMatch(/unlimited/i);
});

test('every plan card lists its seat line and the shared inclusions from billing.py', () => {
  const shared = ['AI contract analysis', 'Contract repository', 'Role-based access control', 'Contract templates', 'PDF export'];
  for (const [id, seats] of [['starter', 5], ['team', 15], ['business', 30]]) {
    const items = [...container.querySelectorAll(`[data-testid="landing-plan-${id}"] ul li`)].map((li) => li.textContent);
    expect(items).toEqual([`Up to ${seats} members`, ...shared]);
  }
});

test('trial footnote matches backend trial facts', () => {
  expect(section().textContent).toContain('Start with a free trial (7 days, up to 3 members and 25 contracts).');
  expect(section().textContent).not.toMatch(/no credit card/i);
});
