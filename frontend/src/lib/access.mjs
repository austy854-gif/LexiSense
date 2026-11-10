// Workspace access state for UI affordances, derived from
// GET /billing/capabilities (backend/routes/billing.py + utils/policy.py).
//
// The UI fails CLOSED: until the authoritative capabilities have loaded, and
// whenever they could not be loaded, every capability is false. There is no
// role-based fallback, so a billing outage or a slow first fetch can never
// expose mutations the server would reject. The server still authorizes
// every request; this only decides what to render.

export const CAPABILITY_KEYS = Object.freeze([
  'canUpload',
  'canDelete',
  'canRestore',
  'canRestoreOwn',
  'canSubmitForReview',
  'canApprove',
  'canReject',
  'canActivate',
  'canInvite',
  'canManageTemplates',
  'canViewAudit',
  'canUpdateAlertSettings',
  'canTriggerAlerts',
  'canEditContract',
  'canEditOwnContract',
]);

// Capabilities that only read data and so stay available in a read-only workspace.
const READ_CAPABILITIES = new Set(['canViewAudit']);

export const NO_CAPABILITIES = Object.freeze(
  Object.fromEntries(CAPABILITY_KEYS.map((key) => [key, false])),
);

export const LOADING_ACCESS = Object.freeze({
  status: 'loading',
  ready: false,
  state: null,
  readOnly: true,
  message: null,
  capabilities: NO_CAPABILITIES,
});

export const FAILED_ACCESS = Object.freeze({
  status: 'error',
  ready: false,
  state: 'unavailable',
  readOnly: true,
  message: 'Workspace access could not be verified. Editing is disabled until it can be.',
  capabilities: NO_CAPABILITIES,
});

/** Normalize a /billing/capabilities payload; only an explicit `true` grants. */
export function normalizeAccess(data) {
  if (!data || typeof data !== 'object' || !data.capabilities || typeof data.capabilities !== 'object') {
    return FAILED_ACCESS;
  }
  const capabilities = { ...NO_CAPABILITIES };
  for (const key of CAPABILITY_KEYS) {
    capabilities[key] = data.capabilities[key] === true;
  }
  return {
    status: 'ready',
    ready: true,
    state: data.state ?? null,
    readOnly: data.readOnly === true,
    message: data.message ?? null,
    capabilities,
  };
}

/** True only when access is loaded, the flag is explicitly true, and the
 * workspace is writable (read capabilities excepted). */
export function hasCapability(access, key) {
  if (!access || access.ready !== true || !access.capabilities) return false;
  if (access.readOnly && !READ_CAPABILITIES.has(key)) return false;
  return access.capabilities[key] === true;
}

/** Org-wide capability, or the "own uploads only" variant for the uploader. */
export function hasContractCapability(access, key, ownKey, contract, user) {
  if (hasCapability(access, key)) return true;
  if (!ownKey || !contract || !user) return false;
  return hasCapability(access, ownKey) && contract.uploadedBy != null && contract.uploadedBy === user.id;
}
