/**
 * Theme token contrast, light AND dark (WCAG 2.x AA).
 *
 * Parses the design tokens in index.css and the badge tones in App.css and checks every
 * text/surface pair the components use. jsdom has no layout or canvas, so axe's
 * color-contrast rule can't run in Jest; the rendered-page axe check for both themes is
 * scripts/axe-themes.mjs (needs a browser). This test is the fast, always-on guard.
 */
const fs = require('fs');
const path = require('path');

const read = (f) => fs.readFileSync(path.join(__dirname, '..', f), 'utf8');
const indexCss = read('index.css');
const appCss = read('App.css');

// ---- colour maths ----
const hslToRgb = ([h, s, l]) => {
  s /= 100; l /= 100;
  const k = (n) => (n + h / 30) % 12;
  const f = (n) => l - s * Math.min(l, 1 - l) * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
  return [f(0), f(8), f(4)].map((v) => v * 255);
};
const lum = (rgb) => {
  const [r, g, b] = rgb.map((v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
const mix = (fg, bg, a) => fg.map((v, i) => v * a + bg[i] * (1 - a));

// ---- token parsing ----
const block = (css, selector) => {
  const re = new RegExp(`(^|[\\s,}])${selector.replace('.', '\\.')}\\s*\\{([^}]*)\\}`, 'm');
  const m = css.match(re);
  if (!m) throw new Error(`no ${selector} block`);
  return m[2];
};
const vars = (body) => Object.fromEntries(
  [...body.matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]),
);
const tokens = {
  root: vars(block(indexCss, ':root')),
  light: vars(block(indexCss, '.light')),
  dark: vars(block(indexCss, '.dark')),
};
const color = (theme, name) => {
  const v = tokens[theme][name];
  if (!v) throw new Error(`--${name} missing in ${theme}`);
  return hslToRgb(v.replace(/%/g, '').split(/\s+/).map(Number));
};

const TEXT_PAIRS = [ // [text token, surface token]
  ['foreground', 'background'], ['card-foreground', 'card'], ['popover-foreground', 'popover'],
  ['primary-foreground', 'primary'], ['secondary-foreground', 'secondary'],
  ['muted-foreground', 'muted'], ['muted-foreground', 'background'], ['muted-foreground', 'card'],
  ['accent-foreground', 'accent'], ['destructive-foreground', 'destructive'],
  ['highlight-foreground', 'highlight'],
  // links / text-primary and inline error text
  ['primary', 'background'], ['primary', 'card'], ['destructive', 'background'], ['destructive', 'card'],
  ['risk-low', 'card'], ['risk-medium', 'card'], ['risk-high', 'card'],
  ['risk-low', 'background'], ['risk-medium', 'background'], ['risk-high', 'background'],
];
const UI_PAIRS = [ // non-text (WCAG 1.4.11, 3:1): form-control borders and the focus ring
  ['input', 'background'], ['input', 'card'], ['ring', 'background'], ['ring', 'card'],
];

describe.each(['light', 'dark'])('%s theme tokens (index.css)', (theme) => {
  test.each(TEXT_PAIRS)('--%s on --%s >= 4.5:1', (fg, bg) => {
    expect(ratio(color(theme, fg), color(theme, bg))).toBeGreaterThanOrEqual(4.5);
  });
  test.each(UI_PAIRS)('--%s against --%s >= 3:1', (fg, bg) => {
    expect(ratio(color(theme, fg), color(theme, bg))).toBeGreaterThanOrEqual(3);
  });
});

describe('theme wiring', () => {
  test('light is the default: :root carries exactly the .light colour tokens', () => {
    const { radius, ...rootColours } = tokens.root; // --radius is layout, defined once on :root
    expect(radius).toBeDefined();
    expect(rootColours).toEqual(tokens.light);
  });
  test('ThemeContext falls back to light', () => {
    const src = read('contexts/ThemeContext.js');
    expect(src).toMatch(/localStorage\.getItem\('theme'\) \|\| 'light'/);
    expect(src).not.toMatch(/\|\| 'dark'/);
  });
  test('dark link colour is not the light-theme #0D4AA5 (216 85% 35%)', () => {
    expect(tokens.dark.primary).not.toBe(tokens.light.primary);
    expect(ratio(color('dark', 'primary'), color('dark', 'background'))).toBeGreaterThanOrEqual(4.5);
  });
  test('App.css does not redefine the theme tokens (it is loaded after index.css and would win)', () => {
    for (const name of Object.keys(tokens.light)) {
      expect(appCss).not.toMatch(new RegExp(`--${name}\\s*:`));
    }
  });
});

describe.each(['light', 'dark'])('%s badge tones (App.css) on the %s surfaces', (theme) => {
  const toneBlock = theme === 'light' ? block(appCss, '.light') : block(appCss, '.dark');
  const raw = vars(toneBlock);
  const tint = Number(raw['ls-tone-tint']);
  const tones = Object.entries(raw)
    .filter(([k]) => k.startsWith('ls-tone-') && k !== 'ls-tone-tint')
    .map(([k, v]) => [k.replace('ls-tone-', ''), v.split(/\s+/).map(Number)]);
  test('tones are defined', () => { expect(tones.length).toBeGreaterThanOrEqual(6); });
  test.each(tones.flatMap(([n, rgb]) => ['background', 'card', 'muted'].map((s) => [n, s, rgb])))(
    '%s badge on --%s >= 4.5:1',
    (_name, surface, rgb) => {
      const s = color(theme, surface);
      expect(ratio(rgb, mix(rgb, s, tint))).toBeGreaterThanOrEqual(4.5);
    },
  );
});
