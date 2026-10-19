// Jest copy of the role checks (CI runs `yarn test`; the .mjs node:test files
// are run separately with `node --test src/lib/*.test.mjs`).
import { canApproveContracts, workflowActionsFor } from './apiHelpers.mjs';

test.each([
  ['viewer', 'review', []],
  ['user', 'review', []],
  ['manager', 'review', ['approve', 'reject']],
  ['admin', 'review', ['approve', 'reject']],
  ['user', 'draft', ['submit_for_review']],
  ['user', 'approved', []],
  ['manager', 'approved', ['activate']],
])('%s on a %s contract sees %j', (role, status, expected) => {
  expect(workflowActionsFor(role, status)).toEqual(expected);
});

test('user role cannot approve', () => {
  expect(canApproveContracts('user')).toBe(false);
});
