import test from 'node:test';
import assert from 'node:assert/strict';

import {
  getErrorInfo,
  isForbidden,
  isNotFound,
  isRateLimited,
  parseTotalCount,
  pageCount,
  pageOffset,
  clampPage,
  isValidRole,
  normalizeRole,
  hasRole,
  canWriteContracts,
  canReadAudit,
  canManageTeam,
  canManageBilling,
  MAX_PAGE_SIZE,
  DEFAULT_PAGE_SIZE,
} from './apiHelpers.mjs';

// ---------------------------------------------------------------------------
// Error envelope
// ---------------------------------------------------------------------------

test('getErrorInfo reads the uniform error envelope', () => {
  const error = {
    response: {
      status: 403,
      data: {
        error: { code: 'forbidden', message: 'Not allowed' },
        requestId: 'req-123',
        status: 403,
      },
    },
  };
  const info = getErrorInfo(error);
  assert.equal(info.code, 'forbidden');
  assert.equal(info.message, 'Not allowed');
  assert.equal(info.status, 403);
  assert.equal(info.requestId, 'req-123');
});

test('getErrorInfo falls back to legacy `detail`', () => {
  const info = getErrorInfo({ response: { status: 404, data: { detail: 'Contract not found' } } });
  assert.equal(info.message, 'Contract not found');
  assert.equal(info.status, 404);
  assert.equal(info.code, null);
});

test('getErrorInfo surfaces validation fields', () => {
  const info = getErrorInfo({
    response: {
      status: 422,
      data: {
        error: {
          code: 'validation_error',
          message: 'Request validation failed',
          details: { fields: [{ field: 'email', message: 'invalid', type: 'value_error' }] },
        },
      },
    },
  });
  assert.equal(info.code, 'validation_error');
  assert.equal(info.fields.length, 1);
  assert.equal(info.fields[0].field, 'email');
});

test('getErrorInfo never throws on a shapeless error', () => {
  const info = getErrorInfo(undefined);
  assert.equal(info.status, null);
  assert.equal(typeof info.message, 'string');
  assert.ok(info.message.length > 0);
});

test('status predicates', () => {
  assert.equal(isForbidden({ response: { status: 403 } }), true);
  assert.equal(isForbidden({ response: { status: 404 } }), false);
  assert.equal(isNotFound({ response: { status: 404 } }), true);
  assert.equal(isRateLimited({ response: { status: 429 } }), true);
  assert.equal(isRateLimited({ response: { status: 500 } }), false);
});

// ---------------------------------------------------------------------------
// X-Total-Count
// ---------------------------------------------------------------------------

test('parseTotalCount reads a Headers-like object', () => {
  const headers = { get: (name) => (name === 'x-total-count' ? '137' : null) };
  assert.equal(parseTotalCount(headers), 137);
});

test('parseTotalCount reads a plain object (case-insensitive)', () => {
  assert.equal(parseTotalCount({ 'X-Total-Count': '42' }), 42);
  assert.equal(parseTotalCount({ 'x-total-count': '0' }), 0);
});

test('parseTotalCount returns null when absent or invalid', () => {
  assert.equal(parseTotalCount(undefined), null);
  assert.equal(parseTotalCount({}), null);
  assert.equal(parseTotalCount({ 'x-total-count': 'not-a-number' }), null);
  assert.equal(parseTotalCount({ 'x-total-count': '-5' }), null);
});

// ---------------------------------------------------------------------------
// Pagination math
// ---------------------------------------------------------------------------

test('pageCount never returns less than 1', () => {
  assert.equal(pageCount(0, 25), 1);
  assert.equal(pageCount(1, 25), 1);
  assert.equal(pageCount(25, 25), 1);
  assert.equal(pageCount(26, 25), 2);
  assert.equal(pageCount(200, 25), 8);
});

test('pageOffset is zero-based', () => {
  assert.equal(pageOffset(1, 25), 0);
  assert.equal(pageOffset(2, 25), 25);
  assert.equal(pageOffset(3, 50), 100);
});

test('clampPage keeps the page inside the valid range', () => {
  assert.equal(clampPage(0, 100, 25), 1);
  assert.equal(clampPage(2, 100, 25), 2);
  assert.equal(clampPage(99, 100, 25), 4);
  assert.equal(clampPage(1, 0, 25), 1);
});

test('page-size constants mirror the backend ceiling', () => {
  assert.equal(MAX_PAGE_SIZE, 200);
  assert.ok(DEFAULT_PAGE_SIZE > 0 && DEFAULT_PAGE_SIZE <= MAX_PAGE_SIZE);
});

// ---------------------------------------------------------------------------
// Role registry
// ---------------------------------------------------------------------------

test('isValidRole accepts only the canonical registry', () => {
  assert.equal(isValidRole('viewer'), true);
  assert.equal(isValidRole('admin'), true);
  assert.equal(isValidRole('superuser'), false);
  assert.equal(isValidRole(undefined), false);
  assert.equal(isValidRole(42), false);
});

test('normalizeRole degrades unknown roles to viewer', () => {
  assert.equal(normalizeRole('manager'), 'manager');
  assert.equal(normalizeRole('root'), 'viewer');
  assert.equal(normalizeRole(null), 'viewer');
});

test('hasRole normalises before checking', () => {
  assert.equal(hasRole('admin', ['admin']), true);
  assert.equal(hasRole('bogus', ['admin']), false);
  assert.equal(hasRole('bogus', ['viewer']), true);
});

test('capability predicates match the backend registry', () => {
  assert.equal(canWriteContracts('viewer'), false);
  assert.equal(canWriteContracts('user'), true);
  assert.equal(canWriteContracts('manager'), true);
  assert.equal(canWriteContracts('admin'), true);

  assert.equal(canReadAudit('viewer'), false);
  assert.equal(canReadAudit('user'), false);
  assert.equal(canReadAudit('manager'), true);
  assert.equal(canReadAudit('admin'), true);

  assert.equal(canManageTeam('manager'), false);
  assert.equal(canManageTeam('admin'), true);

  assert.equal(canManageBilling('manager'), false);
  assert.equal(canManageBilling('admin'), true);
});

test('unknown roles are never more privileged than viewer', () => {
  assert.equal(canWriteContracts('root'), false);
  assert.equal(canReadAudit('root'), false);
  assert.equal(canManageTeam('root'), false);
});
