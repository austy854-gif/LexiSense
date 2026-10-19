// Copy guard for BillingPage: no trial lengths/limits, no "Renewal alerts",
// sentence-case labels. Price values are intentionally not asserted (pricing is
// being drafted separately).
const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'BillingPage.js'), 'utf8');

test.each([
  /7-day/i,
  /\d+\s*-?\s*day/i,
  /trialDaysLeft/,
  /3 per trial/,
  /3-contract/,
  /contracts used/,
  /Renewal alerts/,
])('BillingPage does not contain %s', (pattern) => {
  expect(source).not.toMatch(pattern);
});

test.each([
  'Upgrade to Team Plan',
  'Manage Subscription',
  'Resume Subscription',
  'Reactivate Team Plan',
  'Current Plan',
  'Available Plans',
  'Most Popular',
  'Feature Comparison',
  'Frequently Asked Questions',
  'Free Trial',
  "'Start Free Trial'",
])('BillingPage has no Title Case label "%s"', (label) => {
  expect(source).not.toContain(label);
});

// Quinn's final wording (invites-strings-final.md #35, #40).
test.each([
  'Your plan, payment method and billing history',
  "When your trial ends, your contracts stay in LexiSense. You just won't be able to add new ones until you upgrade to the Team plan.",
])('BillingPage uses final copy "%s"', (text) => {
  expect(source).toContain(text);
});

test('BillingPage no longer says contracts "stay safe"', () => {
  expect(source).not.toContain('stay safe');
});
