// Feature comparison compares Starter, Team and Business (not trial vs paid).
const fs = require('fs');
const path = require('path');
const { PAID_PLANS } = require('../lib/plans');

const source = fs.readFileSync(path.join(__dirname, 'BillingPage.js'), 'utf8');

test('comparison columns come from PAID_PLANS', () => {
  expect(source).toMatch(/PAID_PLANS\.map\(\(p\) => \(\s*<th scope="col"/);
  expect(source).not.toContain('>Free trial</th>');
  expect(source).not.toContain('>Paid plans</th>');
  expect(PAID_PLANS.map((p) => p.name)).toEqual(['Starter', 'Team', 'Business']);
});

test('comparison has a members row from plan seat caps', () => {
  expect(source).toContain('Team members');
  expect(source).toContain('membersLabel(p)');
});
