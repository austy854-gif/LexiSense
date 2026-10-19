import test from 'node:test';
import assert from 'node:assert/strict';
import { isTrialActive, trialEndsLabel } from './billingHelpers.mjs';

const opts = { locale: 'en-US', timeZone: 'UTC' };

test('trialEndsLabel formats the backend trial end date', () => {
  assert.equal(trialEndsLabel('2026-10-25T06:30:00+00:00', opts), 'Trial ends October 25, 2026');
  assert.equal(trialEndsLabel('2026-10-25T06:30:00Z', opts), 'Trial ends October 25, 2026');
});

test('trialEndsLabel hides when the date is unknown or invalid', () => {
  for (const v of [null, undefined, '', 'not-a-date', 123]) {
    assert.equal(trialEndsLabel(v, opts), null);
  }
});

test('isTrialActive', () => {
  const now = new Date('2026-10-18T00:00:00Z');
  assert.equal(isTrialActive('2026-10-25T00:00:00Z', now), true);
  assert.equal(isTrialActive('2026-10-01T00:00:00Z', now), false);
  assert.equal(isTrialActive(null, now), false);
  assert.equal(isTrialActive('garbage', now), false);
});
