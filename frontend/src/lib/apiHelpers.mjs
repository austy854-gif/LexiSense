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

/** Friendly copy for failures that carry no usable server message. */
export const NETWORK_ERROR_MESSAGE = "We couldn't reach LexiSense. Check your connection and try again.";
export const TOO_LARGE_MESSAGE = 'That file is too large. Try a PDF or .txt file under 10 MB.';
export const GENERIC_ERROR_MESSAGE = 'Something went wrong. Please try again.';

/**
 * A response body that is a bare string is only shown when it reads like a
 * human sentence. Proxies and load balancers answer 413/502/504 with HTML
 * pages, and those must never be rendered into a toast.
 */
function asPlainTextMessage(value) {
  const text = asMessage(value);
  if (!text) return null;
  if (/<\/?[a-z!][^>]*>/i.test(text)) return null;
  if (text.length > 300) return null;
  return text;
}

// ---------------------------------------------------------------------------
// Validation errors (LX-ENG-TOAST-01)
// ---------------------------------------------------------------------------

/** Shown when a 422 carries no field we can describe. */
export const VALIDATION_ERROR_MESSAGE = 'Some of the details you entered need another look. Check the form and try again.';
/** A token that fails validation is, to the user, a broken or old link. */
export const INVALID_LINK_MESSAGE = 'This link is invalid or has expired. Request a new one.';

// Human labels for request fields the API validates. Unknown fields fall back
// to a de-camelised version of the last path segment.
const FIELD_LABELS = {
  email: 'Email',
  password: 'Password',
  new_password: 'New password',
  newPassword: 'New password',
  current_password: 'Current password',
  currentPassword: 'Current password',
  name: 'Name',
  full_name: 'Full name',
  fullName: 'Full name',
  organization: 'Organization',
  organizationName: 'Organization name',
  organization_name: 'Organization name',
  role: 'Role',
  title: 'Title',
  file: 'File',
  token: 'Link',
};

const TOKEN_FIELDS = new Set(['token', 'reset_token', 'resetToken', 'invite_token', 'inviteToken']);

function lastSegment(field) {
  if (typeof field !== 'string' || !field) return '';
  const parts = field.split('.').filter((p) => p && !/^\d+$/.test(p) && p !== 'body' && p !== 'query' && p !== 'path');
  return parts.length ? parts[parts.length - 1] : '';
}

/** Readable label for a validation `field` path such as "body.email" or "fullName". */
export function fieldLabel(field) {
  const key = lastSegment(field);
  if (!key) return '';
  if (FIELD_LABELS[key]) return FIELD_LABELS[key];
  const words = key.replace(/_/g, ' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2').trim().toLowerCase();
  if (!words) return '';
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function boundFrom(message, word) {
  // Pydantic phrases bounds as "at least 8 characters" / "at most 72 characters".
  if (typeof message !== 'string') return null;
  const m = message.match(new RegExp(`at ${word} (\\d{1,6})`));
  return m ? Number(m[1]) : null;
}

/**
 * Friendly sentence for one `{field, message, type}` validation entry.
 * Built from the field name and error type only; the server's raw text is
 * never shown (it can name internal models or echo technical detail).
 */
export function friendlyFieldMessage(entry) {
  if (!entry || typeof entry !== 'object') return null;
  const key = lastSegment(entry.field);
  const type = typeof entry.type === 'string' ? entry.type : '';
  if (TOKEN_FIELDS.has(key)) return INVALID_LINK_MESSAGE;
  const label = fieldLabel(entry.field);
  if (!label) return null;
  const lower = label.toLowerCase();

  if (type === 'missing' || type === 'value_error.missing') return `${label} is required.`;
  if (key === 'email' || /email/.test(type)) return 'Enter a valid email address.';
  if (type.startsWith('string_too_short') || type === 'too_short' || type.includes('min_length')) {
    const min = boundFrom(entry.message, 'least');
    if (min === 1) return `${label} is required.`;
    return min ? `${label} must be at least ${min} characters.` : `${label} is too short.`;
  }
  if (type.startsWith('string_too_long') || type === 'too_long' || type.includes('max_length')) {
    const max = boundFrom(entry.message, 'most');
    return max ? `${label} must be at most ${max} characters.` : `${label} is too long.`;
  }
  if (type.startsWith('string_pattern') || type === 'enum' || type.startsWith('literal')) {
    return `Choose a valid ${lower}.`;
  }
  return `Check the ${lower} and try again.`;
}

/**
 * Field-level messages for a validation error: `{ [field]: message }` keyed
 * by the last path segment (e.g. "password"), first message per field wins.
 */
export function validationFieldMessages(fields) {
  const out = {};
  if (!Array.isArray(fields)) return out;
  for (const entry of fields) {
    const key = lastSegment(entry && entry.field) || 'form';
    if (out[key]) continue;
    const msg = friendlyFieldMessage(entry);
    if (msg) out[key] = msg;
  }
  return out;
}

/** One toast-sized sentence summarising a validation error's fields. */
export function friendlyValidationMessage(fields) {
  const messages = [...new Set(Object.values(validationFieldMessages(fields)))];
  if (messages.length === 0) return VALIDATION_ERROR_MESSAGE;
  // A broken link makes any other field moot: say only that.
  if (messages.includes(INVALID_LINK_MESSAGE)) return INVALID_LINK_MESSAGE;
  return messages.slice(0, 2).join(' ');
}

/**
 * Normalise any axios error into a stable, UI-friendly shape.
 *
 * @returns {{code: string|null, message: string, status: number|null,
 *            requestId: string|null, fields: Array|null,
 *            fieldMessages: Object<string,string>|null}}
 */
export function getErrorInfo(error) {
  const response = error && error.response;
  const data = response && response.data;
  const envelope = data && typeof data === 'object' ? data.error : null;

  const code =
    (envelope && typeof envelope.code === 'string' && envelope.code) ||
    (data && typeof data.code === 'string' && data.code) ||
    null;

  const status = response && typeof response.status === 'number' ? response.status : null;

  // Never fall back to `error.message`: for axios errors it is technical text
  // such as "Network Error" or "Request failed with status code 502".
  let message;
  if (!response) {
    // No HTTP response at all: offline, DNS, CORS, timeout or aborted upload.
    message = error && (error.request || error.isAxiosError || error.code)
      ? NETWORK_ERROR_MESSAGE
      : GENERIC_ERROR_MESSAGE;
  } else if (status === 413) {
    // Our API answers 413 with a JSON `detail`; show it. A proxy or load
    // balancer answers with an HTML page (or nothing usable), so fall back.
    message =
      asPlainTextMessage(envelope && envelope.message) ||
      asPlainTextMessage(data && typeof data === 'object' ? data.detail : null) ||
      TOO_LARGE_MESSAGE;
  } else {
    message =
      asMessage(envelope && envelope.message) ||
      asMessage(data && data.detail) ||
      // Bare-string bodies on 5xx come from the server or a proxy
      // ("Internal Server Error", gateway pages), never from our handlers.
      (status < 500 ? asPlainTextMessage(data) : null) ||
      GENERIC_ERROR_MESSAGE;
  }
  const requestId = (data && typeof data.requestId === 'string' && data.requestId) || null;

  const details = envelope && envelope.details;
  const fields = details && Array.isArray(details.fields) ? details.fields : null;

  // LX-ENG-TOAST-01: a 422 validation envelope carries the generic server text
  // "Request validation failed"; replace it with field-level copy.
  let fieldMessages = null;
  if (code === 'validation_error' || (status === 422 && fields)) {
    fieldMessages = validationFieldMessages(fields);
    message = friendlyValidationMessage(fields);
  } else if (status === 422 && Array.isArray(data && data.detail)) {
    // Raw FastAPI shape (no envelope): `detail` is pydantic's error list.
    const legacy = data.detail.map((e) => ({
      field: Array.isArray(e && e.loc) ? e.loc.map(String).join('.') : '',
      message: e && e.msg,
      type: e && e.type,
    }));
    fieldMessages = validationFieldMessages(legacy);
    message = friendlyValidationMessage(legacy);
  }

  return { code, message, status, requestId, fields, fieldMessages };
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
/**
 * Roles allowed to approve, reject or activate a contract. Mirrors
 * PRIVILEGED_TRANSITION_ROLES in backend/services/workflow_service.py and the
 * action map in backend/routes/workflow.py (approve/reject/activate).
 */
export const APPROVER_ROLES = [ROLE_ADMIN, ROLE_MANAGER];

/**
 * Workflow actions per contract status and the roles the server permits for
 * each (backend/routes/workflow.py `action_map`). Keep the two in sync.
 */
export const WORKFLOW_ACTIONS = {
  submit_for_review: { from: 'draft', roles: WRITE_ROLES },
  approve: { from: 'review', roles: APPROVER_ROLES },
  reject: { from: 'review', roles: APPROVER_ROLES },
  activate: { from: 'approved', roles: APPROVER_ROLES },
};

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

export function canApproveContracts(role) {
  return hasRole(role, APPROVER_ROLES);
}

/** True when `role` may perform workflow `action` on a contract in `status`. */
export function canPerformWorkflowAction(role, action, status) {
  const rule = WORKFLOW_ACTIONS[action];
  return Boolean(rule) && rule.from === status && hasRole(role, rule.roles);
}

/** Workflow actions to show for `role` on a contract in `status`. */
export function workflowActionsFor(role, status) {
  return Object.keys(WORKFLOW_ACTIONS).filter((action) =>
    canPerformWorkflowAction(role, action, status),
  );
}
