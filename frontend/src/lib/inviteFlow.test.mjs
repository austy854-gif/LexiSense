import test from 'node:test';
import assert from 'node:assert/strict';

import {
  INVITE_COPY,
  buildAcceptPayload,
  classifyInviteError,
  inviteViewState,
  readInviteToken,
  roleLabel,
  validateInvitePassword,
} from './inviteFlow.mjs';

const err = (status, detail = 'x') => ({ response: { status, data: { detail } } });
const preview = { email: 'new@example.com', organizationName: 'Acme', role: 'user' };

test('readInviteToken reads ?token= and tolerates missing values', () => {
  assert.equal(readInviteToken('?token=abc'), 'abc');
  assert.equal(readInviteToken('?token=a%2Bb'), 'a+b');
  assert.equal(readInviteToken('?token='), '');
  assert.equal(readInviteToken(''), '');
  assert.equal(readInviteToken(undefined), '');
});

test('classifyInviteError maps backend status codes', () => {
  assert.equal(classifyInviteError(err(404)), 'invalid');
  assert.equal(classifyInviteError(err(400)), 'expired');
  assert.equal(classifyInviteError(err(409)), 'used');
  assert.equal(classifyInviteError(err(429)), 'rate_limited');
  assert.equal(classifyInviteError(err(422)), 'validation');
  assert.equal(classifyInviteError(err(500)), 'error');
  assert.equal(classifyInviteError(new Error('Network Error')), 'error');
});

test('inviteViewState: missing token wins over everything', () => {
  assert.equal(inviteViewState({ token: '', authLoading: true, user: null, preview: null, errorKind: null }), 'missing');
});

test('inviteViewState: error kinds', () => {
  for (const kind of ['invalid', 'expired', 'used', 'rate_limited', 'error']) {
    assert.equal(inviteViewState({ token: 't', authLoading: false, user: null, preview: null, errorKind: kind }), kind);
  }
});

test('inviteViewState: loading until preview and auth are ready', () => {
  assert.equal(inviteViewState({ token: 't', authLoading: false, user: null, preview: null, errorKind: null }), 'loading');
  assert.equal(inviteViewState({ token: 't', authLoading: true, user: null, preview, errorKind: null }), 'loading');
});

test('inviteViewState: logged out shows the form', () => {
  assert.equal(inviteViewState({ token: 't', authLoading: false, user: null, preview, errorKind: null }), 'form');
});

test('inviteViewState: a 422 on submit keeps the form', () => {
  assert.equal(inviteViewState({ token: 't', authLoading: false, user: null, preview, errorKind: 'validation' }), 'form');
});

test('inviteViewState: signed in as the invitee vs someone else', () => {
  const same = { email: 'NEW@example.com ' };
  const other = { email: 'boss@example.com' };
  assert.equal(inviteViewState({ token: 't', authLoading: false, user: same, preview, errorKind: null }), 'signed_in_same');
  assert.equal(inviteViewState({ token: 't', authLoading: false, user: other, preview, errorKind: null }), 'signed_in_other');
});

test('validateInvitePassword', () => {
  assert.equal(validateInvitePassword('short', 'short'), INVITE_COPY.passwordTooShort);
  assert.equal(validateInvitePassword('longenough', 'different1'), INVITE_COPY.passwordsDontMatch);
  assert.equal(validateInvitePassword('longenough', 'longenough'), null);
  assert.equal(validateInvitePassword('12345678', '12345678'), null);
});

test('buildAcceptPayload trims names and omits blanks', () => {
  assert.deepEqual(buildAcceptPayload('t', { password: 'p', firstName: ' Ada ', lastName: '' }), {
    token: 't', password: 'p', firstName: 'Ada',
  });
  assert.deepEqual(buildAcceptPayload('t', { password: 'p' }), { token: 't', password: 'p' });
});

test('roleLabel is sentence case and degrades unknown roles to Viewer', () => {
  assert.equal(roleLabel('manager'), 'Manager');
  assert.equal(roleLabel('admin'), 'Admin');
  assert.equal(roleLabel('superuser'), 'Viewer');
});
