#!/usr/bin/env node
/**
 * axe colour-contrast check of the built app in BOTH themes (AXE_ALL=1: all WCAG 2.x A/AA rules).
 *
 * Serves ./build (run `yarn build` first), mocks the /api/v1 backend with fixture data so
 * every page renders real content (badges, tables, charts), then, for each theme, loads each
 * main page with localStorage.theme set and runs axe-core (from node_modules) in Chromium.
 *
 *   node scripts/axe-themes.mjs                  # all pages, light + dark
 *   AXE_PAGES=/,/contracts node scripts/axe-themes.mjs
 *   AXE_ALL=1 node scripts/axe-themes.mjs        # every WCAG 2.0/2.1 A+AA rule, not just contrast
 *   AXE_JSON=out.json node scripts/axe-themes.mjs     # write the full results
 *
 * Needs the `playwright` package and a Chromium it can launch. It is deliberately not a
 * project dependency (keeps the lockfiles untouched); point PLAYWRIGHT_MODULE at an install,
 * e.g. PLAYWRIGHT_MODULE=/path/to/node_modules/playwright. Exits 1 on any violation, or if a
 * page crashes to the error boundary (it would otherwise pass with nothing on it).
 */
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const buildDir = path.join(root, 'build');
const require = createRequire(path.join(root, 'package.json'));

async function loadPlaywright() {
  const candidates = [process.env.PLAYWRIGHT_MODULE, 'playwright', 'playwright-core'].filter(Boolean);
  for (const c of candidates) {
    try {
      const spec = path.isAbsolute(c) ? pathToFileURL(require.resolve(c)).href : c;
      const mod = await import(spec);
      return mod.chromium ? mod : mod.default;
    } catch { /* try next */ }
  }
  console.error('axe-themes: playwright not found. Set PLAYWRIGHT_MODULE=/path/to/node_modules/playwright');
  process.exit(2);
}

if (!fs.existsSync(path.join(buildDir, 'index.html'))) {
  console.error('axe-themes: no build/ found, run `yarn build` first');
  process.exit(2);
}
const axeSource = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

// ---------- fixtures ----------
const now = Date.now();
const iso = (days) => new Date(now + days * 86400000).toISOString();
const user = { id: 'u1', email: 'austin@example.com', firstName: 'Austin', lastName: 'Auger', role: 'admin', isActive: true };
const contracts = [
  ['c1', 'Master Services Agreement', 'Acme Corp', 'active', 'low', 'service'],
  ['c2', 'Mutual NDA', 'Globex', 'draft', 'medium', 'nda'],
  ['c3', 'Office Lease 2026', 'Initech', 'expired', 'high', 'lease'],
  ['c4', 'Supplier Agreement', 'Umbrella', 'pending_approval', 'medium', 'vendor'],
  ['c5', 'Employment Contract', 'Hooli', 'approved', 'low', 'employment'],
].map(([id, title, counterparty, status, riskLevel, contractType], i) => ({
  id, title, counterparty, status, riskLevel, contractType,
  fileName: `${id}.pdf`, fileSize: 120000 + i * 1000, createdAt: iso(-10 - i),
  effectiveDate: iso(-100), expiryDate: iso(20 + i * 30), value: 50000, uploaderEmail: user.email,
}));
const detail = {
  ...contracts[0],
  aiAnalysisStatus: 'completed',
  aiAnalysis: {
    summary: 'A three-year services agreement with standard terms.',
    parties: ['Acme Corp', 'LexiSense Inc.'],
    risks: [
      { title: 'Uncapped liability', description: 'No cap on indemnity.', severity: 'high', level: 'high' },
      { title: 'Auto-renewal', description: 'Renews unless cancelled 90 days prior.', severity: 'medium', level: 'medium' },
      { title: 'Governing law', description: 'Alberta law.', severity: 'low', level: 'low' },
    ],
    keyTerms: [{ term: 'Term', value: '3 years' }, { term: 'Payment', value: 'Net 30' }],
    recommendations: ['Negotiate a liability cap.', 'Calendar the renewal notice date.'],
  },
};
const routes = [
  [/\/auth\/me$/, user],
  [/\/notifications\/unread-count$/, { count: 2 }],
  [/\/notifications$/, [
    { id: 'n1', title: 'Contract expiring', message: 'Office Lease expires in 20 days', read: false, isRead: false, createdAt: iso(-1), link: '/contracts/c3' },
    { id: 'n2', title: 'Approval requested', message: 'Supplier Agreement needs approval', read: true, isRead: true, createdAt: iso(-2) },
  ]],
  [/\/dashboard\/stats$/, {
    overview: { totalContracts: 5, activeContracts: 2, expiringSoon: 1, highRisk: 1, teamMembers: 3 },
    byRisk: { low: 2, medium: 2, high: 1 }, byType: { service: 1, nda: 1, lease: 1, vendor: 1, employment: 1 },
    byStatus: { active: 2, draft: 1, expired: 1, pending_approval: 1 }, recentContracts: contracts,
  }],
  [/\/dashboard\/activity$/, []],
  [/\/contracts\/workflow\/states$/, { states: ['draft', 'pending_approval', 'approved', 'active', 'expired'] }],
  [/\/contracts\/c\d\/versions$/, [{ id: 'v1', version: 1, changeReason: 'Initial upload', changedByEmail: user.email, createdAt: iso(-10) }]],
  [/\/contracts\/c\d\/workflow\/history$/, { currentState: 'active', availableActions: [], history: [] }],
  [/\/contracts\/c\d$/, detail],
  [/\/contracts$/, contracts, { 'x-total-count': String(contracts.length) }],
  [/\/templates$/, [
    { id: 't1', name: 'Standard NDA', description: 'Mutual non-disclosure', contractType: 'nda', isPublic: true, tags: ['nda', 'standard'], content: 'NDA' },
    { id: 't2', name: 'Services MSA', description: 'Master services', contractType: 'service', isPublic: false, tags: ['msa'], content: 'MSA' },
  ]],
  [/\/analytics\/overview$/, {
    summary: { totalContracts: 5, activeContracts: 2, draftContracts: 1, expiredContracts: 1, highRiskContracts: 1, teamMembers: 3 },
    riskDistribution: { low: 2, medium: 2, high: 1 },
    byType: [{ type: 'service', count: 1 }, { type: 'nda', count: 1 }, { type: 'lease', count: 1 }],
    monthlyTrend: [{ month: '2026-08', count: 1 }, { month: '2026-09', count: 2 }, { month: '2026-10', count: 2 }],
    expiring: { next7Days: 0, next30Days: 1, next90Days: 3 },
    topUploaders: [{ email: user.email, user: 'Austin Auger', uploads: 5, count: 5 }],
  }],
  [/\/alerts\/settings$/, { enabled: true, alertDays: [30, 14, 7, 1], emailEnabled: true }],
  [/\/alerts\/expiring$/, { contracts: contracts.slice(0, 3).map((c, i) => ({ ...c, daysRemaining: 5 + i * 10 })) }],
  [/\/alerts\/history$/, { alerts: [{ id: 'a1', contractTitle: 'Office Lease 2026', daysBeforeExpiry: 30, sentAt: iso(-1), createdAt: iso(-1), status: 'sent' }] }],
  [/\/team\/members$/, [
    user,
    { id: 'u2', email: 'jackson@example.com', firstName: 'Jackson', lastName: 'Lee', role: 'manager', isActive: true, lastLogin: iso(-1) },
    { id: 'u3', email: 'sam@example.com', firstName: 'Sam', lastName: 'Ng', role: 'user', isActive: true, lastLogin: iso(-3) },
  ]],
  [/\/team\/invitations$/, [{ id: 'i1', email: 'new@example.com', role: 'user', createdAt: iso(-1), expiresAt: iso(6), status: 'pending' }]],
  [/\/audit$/, [
    { id: 'l1', action: 'contract_uploaded', resourceType: 'contract', resourceTitle: 'Mutual NDA', userEmail: user.email, createdAt: iso(-1), details: {} },
    { id: 'l2', action: 'contract_deleted', resourceType: 'contract', resourceTitle: 'Old NDA', userEmail: user.email, createdAt: iso(-2), details: {} },
    { id: 'l3', action: 'user_login', resourceType: 'user', resourceTitle: user.email, userEmail: user.email, createdAt: iso(-3), details: {} },
  ], { 'x-total-count': '3' }],
  [/\/billing\/subscription$/, { subscriptionTier: 'trial', billingStatus: 'trialing', trialEndDate: iso(10), trialContractsUsed: 2, trialContractsLimit: 10, currentPeriodEnd: iso(10), cancelAtPeriodEnd: false }],
  [/\/billing\/plans$/, { plans: [
    { id: 'starter', name: 'Starter', price: 49, interval: 'month', priceId: 'price_s', features: ['10 contracts', 'Email alerts'], cta: 'Choose Starter' },
    { id: 'pro', name: 'Professional', price: 149, interval: 'month', priceId: 'price_p', features: ['Unlimited contracts', 'AI analysis'], cta: 'Choose Professional', popular: true },
  ] }],
];

// ---------- static server ----------
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.json': 'application/json', '.woff2': 'font/woff2' };
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  let file = path.join(buildDir, decodeURIComponent(url.pathname));
  if (!file.startsWith(buildDir) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(buildDir, 'index.html');
  res.writeHead(200, { 'content-type': types[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const origin = `http://127.0.0.1:${server.address().port}`;

const PUBLIC = ['/login', '/register'];
const APP = ['/', '/contracts', '/contracts/c1', '/templates', '/analytics', '/alerts', '/team', '/audit', '/billing'];
const pages = process.env.AXE_PAGES ? process.env.AXE_PAGES.split(',') : [...PUBLIC, ...APP];
const themes = (process.env.AXE_THEMES || 'light,dark').split(',');

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const report = [];
let failures = 0;
let crashes = 0;

for (const theme of themes) {
  for (const route of pages) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
    const authed = !PUBLIC.includes(route);
    await ctx.addInitScript(([t, u, a]) => {
      localStorage.setItem('theme', t);
      if (a) { localStorage.setItem('token', 'test-token'); localStorage.setItem('user', JSON.stringify(u)); }
    }, [theme, user, authed]);
    await ctx.route(/^https?:\/\/(fonts\.googleapis\.com|fonts\.gstatic\.com|assets\.emergent\.sh)\//, (r) => r.abort());
    await ctx.route(/\/api\/v1\//, (r) => {
      const p = new URL(r.request().url()).pathname;
      const hit = routes.find(([re]) => re.test(p));
      if (!hit) return r.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return r.fulfill({ status: 200, contentType: 'application/json', headers: { 'access-control-expose-headers': 'x-total-count', ...(hit[2] || {}) }, body: JSON.stringify(hit[1]) });
    });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    // Third-party fonts/scripts are blocked on purpose; ignore their load failures.
    page.on('console', (m) => { if (m.type() === 'error' && !m.text().includes('ERR_FAILED')) errors.push(m.text().slice(0, 300)); });
    await page.goto(origin + route, { waitUntil: 'networkidle' });
    await page.waitForTimeout(400); // let charts/animations settle
    const htmlClass = await page.evaluate(() => document.documentElement.className);
    // The Sentry error boundary swaps the whole app for one line of text; axe would "pass" it.
    const crashed = await page.evaluate(() => (document.getElementById('root')?.innerText || '').trim() === 'Something went wrong');
    if (crashed) {
      crashes += 1;
      const why = errors[0] || 'unknown error';
      console.log(`CRASH [${theme}] ${route}  page did not render, not checked: ${why.split('\n')[0]}`);
      report.push({ theme, route, htmlClass, crashed: true, errors });
      await ctx.close();
      continue;
    }
    await page.addScriptTag({ content: axeSource });
    const result = await page.evaluate(async (onlyContrast) => {
      const opts = onlyContrast
        ? { runOnly: { type: 'rule', values: ['color-contrast'] } }
        : { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } };
      const r = await window.axe.run(document, opts);
      const slim = (v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.map((n) => ({ target: n.target.join(' '), summary: n.failureSummary?.split('\n').slice(1).join(' ').trim() })) });
      const inc = r.incomplete.find((v) => v.id === 'color-contrast');
      // "incomplete" = axe could not decide (text over gradients/images, SVG chart text, ...): review by hand.
      const incomplete = inc ? inc.nodes.map((n) => ({ target: n.target.join(' '), reason: (n.any[0]?.message || '').slice(0, 160) })) : [];
      return { violations: r.violations.map(slim), incompleteContrast: incomplete.length, incomplete, passes: r.passes.length };
    }, !process.env.AXE_ALL);
    if (process.env.AXE_SHOTS) await page.screenshot({ path: path.join(process.env.AXE_SHOTS, `${theme}${route.replace(/\//g, '_') || '_'}.png`), fullPage: true });
    const n = result.violations.reduce((s, v) => s + v.nodes.length, 0);
    failures += n;
    report.push({ theme, route, htmlClass, errors, ...result });
    console.log(`${n ? 'FAIL' : 'ok  '} [${theme}] ${route}  violations=${n} contrast-incomplete=${result.incompleteContrast}${htmlClass.includes(theme) ? '' : `  (html class "${htmlClass}")`}${errors.length ? `  pageerrors=${errors.length}` : ''}`);
    for (const v of result.violations) for (const node of v.nodes.slice(0, 6)) console.log(`       ${v.id}: ${node.target} :: ${node.summary}`);
    await ctx.close();
  }
}

await browser.close();
server.close();
if (process.env.AXE_JSON) fs.writeFileSync(process.env.AXE_JSON, JSON.stringify(report, null, 2));
console.log(`\naxe-themes: ${failures ? `${failures} violating node(s)` : 'no violations'}${crashes ? `, ${crashes} page(s) crashed and were not checked` : ''}`);
process.exit(failures || crashes ? 1 : 0);
