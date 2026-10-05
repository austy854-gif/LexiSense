/**
 * Pure, framework-free helpers that pin the frontend to the hardened backend
 * contract landed on `feat/backend-architecture-improvements`.
 *
 * Why a separate module:
 *   The backend now returns a uniform error envelope
 *   (`{ error: { code, message, details }, requestId, status }`), bounded
 *   pagination with an `X-Total-Count` header, and a single role registry
 *   (`viewer` < `user` < `manager` < `admin`). The React pages previously
 *   branched on HTTP status and parsed prose, and hard-coded role strings in
 *   several places. These helpers are the one place that knows the contract,
 *   and they are plain ESM so they can be unit-tested with `node --test`
 *   without a bundler or a DOM.
 */

// ---------------------------------------------------------------------------
// Error envelope
// ---------------------------------------------------------------------------

function asMessage(value) {
  return typeof value === 'string' && value.trim() ? value : null;
}

/**
 * Normalise any axios error into a stable, UI-friendly shape.
 *
 * @returns {{code: string|null, message: string, status: number|null,
 *            requestId: string|null, fields: Array|null}}
 */
export function getErrorInfo(error) {
  const response = error && error.response;
  const data = response && response.data;
  const envelope = data && typeof data === 'object' ? data.error : null;

  const code =
    (envelope && typeof envelope.code === 'string' && envelope.code) ||
    (data && typeof data.code === 'string' && data.code) ||
    null;

  const message =
    asMessage(envelope && envelope.message) ||
    asMessage(data && data.detail) ||
    asMessage(data) ||
    asMessage(error && error.message) ||
    'Something went wrong. Please try again.';

  const status = response && typeof response.status === 'number' ? response.status : null;
  const requestId = (data && typeof data.requestId === 'string' && data.requestId) || null;

  const details = envelope && envelope.details;
  const fields = details && Array.isArray(details.fields) ? details.fields : null;

  return { code, message, status, requestId, fields };
}

export function isForbidden(error) {
  return getErrorInfo(error).status === 403;
}

export function isNotFound(error) {
  return getErrorInfo(error).status === 404;
}

export function isRateLimited(error) {
  return getErrorInfo(error).status === 429;
}

/**
 * Read the total record count the backend exposes on list endpoints.
 * Accepts an axios response `headers` object (or a plain object).
 *
 * @returns {number|null} the parsed count, or null when absent/invalid.
 */
export function parseTotalCount(headers) {
  if (!headers) return null;
  let raw;
  if (typeof headers.get === 'function') {
    raw = headers.get('x-total-count');
  } else {
    raw = headers['x-total-count'] ?? headers['X-Total-Count'];
  }
  if (raw === undefined || raw === null || raw === '') return null;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

// ---------------------------------------------------------------------------
// Pagination math (mirrors backend MAX_PAGE_SIZE / DEFAULT_PAGE_SIZE)
// ---------------------------------------------------------------------------

/** Hard server-side ceiling for any single list response. */
export const MAX_PAGE_SIZE = 200;
/** Default page size when the caller does not specify one. */
export const DEFAULT_PAGE_SIZE = 25;

/** Total number of pages for `total` records at `pageSize` (never < 1). */
export function pageCount(total, pageSize) {
  const t = Number(total) || 0;
  const s = Number(pageSize) || 1;
  if (t <= 0) return 1;
  return Math.max(1, Math.ceil(t / s));
}

/** Zero-based offset for a 1-based `page`. */
export function pageOffset(page, pageSize) {
  const p = Math.max(1, Number(page) || 1);
  const s = Math.max(1, Number(pageSize) || 1);
  return (p - 1) * s;
}

/** Clamp a requested page into the valid range for the current total. */
export function clampPage(page, total, pageSize) {
  const p = Math.max(1, Number(page) || 1);
  return Math.min(p, pageCount(total, pageSize));
}

// ---------------------------------------------------------------------------
// Role registry (mirrors backend utils/rbac.py)
// ---------------------------------------------------------------------------

export const ROLE_VIEWER = 'viewer';
export const ROLE_USER = 'user';
export const ROLE_MANAGER = 'manager';
export const ROLE_ADMIN = 'admin';

/** Canonical roles, ordered least -> most privileged. */
export const ROLES = [ROLE_VIEWER, ROLE_USER, ROLE_MANAGER, ROLE_ADMIN];
/** Least-privileged role: what an unknown/corrupt role degrades to. */
export const DEFAULT_ROLE = ROLE_VIEWER;
/** Roles allowed to change membership, roles and organisation settings. */
export const ADMIN_ROLES = [ROLE_ADMIN];
/** Roles allowed to read organisation-wide audit data. */
export const AUDIT_READER_ROLES = [ROLE_ADMIN, ROLE_MANAGER];
/** Roles allowed to author/modify contracts (still subject to ownership). */
export const WRITE_ROLES = [ROLE_USER, ROLE_MANAGER, ROLE_ADMIN];

export function isValidRole(role) {
  return typeof role === 'string' && ROLES.includes(role);
}

/** Map an unknown/invalid role to the least-privileged role. */
export function normalizeRole(role) {
  return isValidRole(role) ? role : DEFAULT_ROLE;
}

/** True when `role` (normalised) is one of `allowed`. */
export function hasRole(role, allowed) {
  return Array.isArray(allowed) && allowed.includes(normalizeRole(role));
}

export function canWriteContracts(role) {
  return hasRole(role, WRITE_ROLES);
}

export function canReadAudit(role) {
  return hasRole(role, AUDIT_READER_ROLES);
}

export function canManageTeam(role) {
  return hasRole(role, ADMIN_ROLES);
}

export function canManageBilling(role) {
  return hasRole(role, ADMIN_ROLES);
}
