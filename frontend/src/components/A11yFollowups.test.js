globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import fs from 'fs';
import path from 'path';
import axe from 'axe-core';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ContractsPage from '../pages/ContractsPage';
import { PaginationBar } from './PaginationBar';
import { contractsAPI } from '../api';

jest.mock('../api', () => ({
  notificationsAPI: { list: jest.fn(), unreadCount: jest.fn(), markRead: jest.fn(), markAllRead: jest.fn() },
  contractsAPI: { list: jest.fn(), upload: jest.fn(), delete: jest.fn(), bulkUpload: jest.fn() },
  exportAPI: {},
}));
jest.mock('./Layout', () => ({ Layout: ({ children }) => <main>{children}</main> }));
jest.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { firstName: 'Alex', email: 'alex@example.com', role: 'admin' }, logout: jest.fn() }),
}));

const contract = {
  id: 'c1', title: 'Service Agreement', counterparty: 'Acme Corp', contractType: 'service', status: 'active',
  riskLevel: 'medium', createdAt: '2026-09-01T00:00:00Z',
};

let container;
let root;

beforeEach(() => {
  contractsAPI.list.mockResolvedValue({ data: [contract], headers: { 'x-total-count': '30' } });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(ui) {
  await act(async () => { root.render(<MemoryRouter>{ui}</MemoryRouter>); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const RULES = ['button-name', 'link-name', 'aria-valid-attr-value', 'label-content-name-mismatch', 'aria-input-field-name'];
async function axeViolations() {
  const result = await axe.run(container, { runOnly: { type: 'rule', values: RULES } });
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(' | ')}`);
}
const byTestId = (id) => container.querySelector(`[data-testid="${id}"]`);

describe('Contracts page names', () => {
  test('filter selects are named in sentence case', async () => {
    await render(<ContractsPage />);
    expect(byTestId('status-filter').getAttribute('aria-label')).toBe('Filter by status');
    expect(byTestId('type-filter').getAttribute('aria-label')).toBe('Filter by type');
    await act(async () => { byTestId('advanced-filters-toggle').click(); });
    expect(byTestId('risk-filter').getAttribute('aria-label')).toBe('Filter by risk level');
    expect(byTestId('expiring-filter').getAttribute('aria-label')).toBe('Filter by expiry date');
  });

  test('row actions name the contract', async () => {
    await render(<ContractsPage />);
    expect(byTestId('view-c1').getAttribute('aria-label')).toBe('View Service Agreement');
    expect(byTestId('delete-c1').getAttribute('aria-label')).toBe('Delete Service Agreement');
    expect(container.querySelector('[aria-label="Export Service Agreement as PDF"]')).not.toBeNull();
  });

  test('axe: no unnamed controls or name/label mismatches (Advanced closed and open)', async () => {
    await render(<ContractsPage />);
    expect(await axeViolations()).toEqual([]);
    await act(async () => { byTestId('advanced-filters-toggle').click(); });
    expect(await axeViolations()).toEqual([]);
  });
});

describe('PaginationBar', () => {
  test('previous/next names contain their visible text', async () => {
    await render(<PaginationBar page={2} totalPages={3} total={30} pageSize={10} onPageChange={() => {}} onPageSizeChange={() => {}} />);
    for (const id of ['page-prev', 'page-next']) {
      const b = byTestId(id);
      // axe matches whole words, so the visible word must appear as a word in the name.
      expect(b.getAttribute('aria-label').toLowerCase().split(/\s+/)).toContain(b.textContent.trim().toLowerCase());
    }
    expect(byTestId('page-prev').getAttribute('aria-label')).toBe('Previous page');
    expect(byTestId('page-next').getAttribute('aria-label')).toBe('Next page');
    expect(await axeViolations()).toEqual([]);
  });
});

describe('badge tones (App.css)', () => {
  // WCAG relative-luminance contrast, text on its own tint composited over each surface.
  const lum = (rgb) => {
    const [r, g, b] = rgb.map((v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
  const mix = (fg, bg, a) => fg.map((v, i) => v * a + bg[i] * (1 - a));
  const hsl = (h, s, l) => {
    s /= 100; l /= 100;
    const k = (n) => (n + h / 30) % 12;
    const f = (n) => l - s * Math.min(l, 1 - l) * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
    return [f(0), f(8), f(4)].map((v) => v * 255);
  };
  const css = fs.readFileSync(path.join(__dirname, '../App.css'), 'utf8');
  const block = (selector) => css.slice(css.indexOf(selector)).split('}')[0];
  const tones = (b) => Object.fromEntries([...b.matchAll(/--ls-tone-(\w+): (\d+) (\d+) (\d+);/g)].map((m) => [m[1], m.slice(2).map(Number)]));
  const tint = (b) => Number(b.match(/--ls-tone-tint: ([\d.]+)/)[1]);
  // Current theme surfaces (index.css): card, page background, muted (row hover).
  const surfaces = {
    dark: [hsl(220, 13, 12), hsl(220, 15, 8), hsl(220, 12, 16)],
    light: [[255, 255, 255], [255, 255, 255], hsl(220, 15, 96)],
  };
  const themes = { dark: block(':root,\n.dark {'), light: block('\n.light {') };

  test.each(['dark', 'light'])('%s: every tone is >= 4.5:1 on its tint over card, page and muted', (theme) => {
    const t = tones(themes[theme]);
    expect(Object.keys(t).sort()).toEqual(['blue', 'emerald', 'gray', 'green', 'orange', 'purple', 'red', 'yellow']);
    for (const [name, fg] of Object.entries(t)) {
      for (const s of surfaces[theme]) {
        expect([name, ratio(fg, mix(fg, s, tint(themes[theme]))) >= 4.5]).toEqual([name, true]);
      }
    }
  });
});
