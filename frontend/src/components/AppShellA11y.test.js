globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import fs from 'fs';
import path from 'path';
import axe from 'axe-core';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { Layout } from './Layout';
import ContractsPage from '../pages/ContractsPage';
import { notificationsAPI, contractsAPI } from '../api';

jest.mock('../api', () => ({
  notificationsAPI: { list: jest.fn(), unreadCount: jest.fn(), markRead: jest.fn(), markAllRead: jest.fn() },
  contractsAPI: { list: jest.fn(), upload: jest.fn(), delete: jest.fn(), bulkUpload: jest.fn() },
  exportAPI: {},
}));

let mockTheme = 'light';
const mockToggle = jest.fn();
jest.mock('../contexts/ThemeContext', () => ({ useTheme: () => ({ theme: mockTheme, toggleTheme: mockToggle }) }));
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { firstName: 'Alex', lastName: 'Rivera', email: 'alex@example.com', role: 'admin' }, logout: jest.fn() }),
}));

let container;
let root;

beforeEach(() => {
  notificationsAPI.list.mockResolvedValue({ data: [] });
  notificationsAPI.unreadCount.mockResolvedValue({ data: { count: 3 } });
  contractsAPI.list.mockResolvedValue({ data: [], headers: { 'x-total-count': '0' } });
  mockTheme = 'light';
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function renderAt(ui, pathname = '/') {
  await act(async () => {
    root.render(<MemoryRouter initialEntries={[pathname]}>{ui}</MemoryRouter>);
  });
  await act(async () => {}); // let the notification fetch settle
}

// Same rule set axe flagged in the browser, minus color-contrast (jsdom has no layout).
async function axeViolations(rules = ['button-name', 'link-name', 'aria-valid-attr-value', 'label-content-name-mismatch']) {
  const result = await axe.run(container, { runOnly: { type: 'rule', values: rules } });
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(' | ')}`);
}

const shellButton = (testid) => container.querySelector(`[data-testid="${testid}"]`);

describe('app shell accessible names', () => {
  test('every shell button has an accessible name (axe)', async () => {
    await renderAt(<Layout><p>content</p></Layout>);
    expect(await axeViolations()).toEqual([]);
  });

  test('icon-only buttons are labelled in sentence case', async () => {
    await renderAt(<Layout><p>content</p></Layout>);
    const labels = [...container.querySelectorAll('header button[aria-label], aside button[aria-label]')].map((b) =>
      b.getAttribute('aria-label'),
    );
    expect(labels).toEqual(expect.arrayContaining(['Close menu', 'Open menu', 'Switch to dark mode', 'Notifications, 3 unread']));
    labels.forEach((l) => expect(l).toMatch(/^[A-Z][^A-Z]*$/)); // sentence case: only the first letter capitalised
    // The icons themselves are hidden so they don't add noise to the name.
    container.querySelectorAll('header button svg, aside button svg').forEach((svg) => expect(svg.getAttribute('aria-hidden')).toBe('true'));
  });

  test('theme toggle names the mode it switches to', async () => {
    mockTheme = 'dark';
    await renderAt(<Layout><p>content</p></Layout>);
    expect(shellButton('theme-toggle').getAttribute('aria-label')).toBe('Switch to light mode');
  });

  test('notifications label drops the count when nothing is unread', async () => {
    notificationsAPI.unreadCount.mockResolvedValue({ data: { count: 0 } });
    await renderAt(<Layout><p>content</p></Layout>);
    expect(shellButton('notifications-bell').getAttribute('aria-label')).toBe('Notifications');
  });

  test('account menu is named, with the initials hidden from the name', async () => {
    await renderAt(<Layout><p>content</p></Layout>);
    const menu = shellButton('user-menu');
    expect(menu.querySelector('.sr-only').textContent).toBe('Account menu');
    expect(menu.querySelector('[aria-hidden="true"]').textContent).toBe('AR');
  });

  test('mobile menu button reports whether the sidebar is open', async () => {
    await renderAt(<Layout><p>content</p></Layout>);
    const open = container.querySelector('header button[aria-label="Open menu"]');
    expect(open.getAttribute('aria-expanded')).toBe('false');
    act(() => open.click());
    expect(open.getAttribute('aria-expanded')).toBe('true');
  });
});

describe('avatar initials contrast (WCAG AA, brand Steady Blue values)', () => {
  const css = fs.readFileSync(path.join(__dirname, 'avatar-initials.css'), 'utf8');
  const block = (selector) => {
    const m = css.match(new RegExp(`${selector}\\s*\\{([^}]*)\\}`));
    const get = (name) => m[1].match(new RegExp(`--ls-avatar-${name}:\\s*(#[0-9A-Fa-f]{6})`))[1];
    return { bg: get('bg'), fg: get('fg') };
  };
  const lum = (hex) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) =>
      c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
    );
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };

  test.each([
    ['dark', '\\.dark', '#1F2F47', '#6FA3F5'],
    ['light', '\\.light', '#E7EEF9', '#0D4AA5'],
  ])('%s theme uses brand colours at 4.5:1 or better', (_, selector, bg, fg) => {
    const c = block(selector);
    expect(c).toEqual({ bg, fg });
    expect(ratio(c.fg, c.bg)).toBeGreaterThanOrEqual(4.5);
  });

  test('both avatars in the shell use the themed initials class', async () => {
    await renderAt(<Layout><p>content</p></Layout>);
    const initials = [...container.querySelectorAll('.ls-avatar-initials')];
    expect(initials).toHaveLength(2);
    initials.forEach((el) => expect(el.className).not.toMatch(/text-primary|bg-primary/));
  });
});

describe('contracts advanced filters toggle', () => {
  test('aria-controls points at an element that exists, open or closed', async () => {
    await renderAt(<ContractsPage />, '/contracts');
    const toggle = shellButton('advanced-filters-toggle');
    const target = () => document.getElementById(toggle.getAttribute('aria-controls'));
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(target()).not.toBeNull();
    expect(await axeViolations(['aria-valid-attr-value'])).toEqual([]);

    await act(async () => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(target()).not.toBeNull();
    expect(target().textContent).toMatch(/Clear All Filters/);
    expect(await axeViolations(['aria-valid-attr-value'])).toEqual([]);

    await act(async () => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(target().textContent).not.toMatch(/Clear All Filters/);
  });
});
