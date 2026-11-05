// Test-only: build the access object AuthContext exposes, using the same role
// matrix as backend/utils/policy.py (capability_map).
import { normalizeAccess } from '../lib/access.mjs';

const MATRIX = {
  admin: ['canUpload', 'canDelete', 'canRestore', 'canRestoreOwn', 'canSubmitForReview', 'canApprove', 'canReject',
    'canActivate', 'canInvite', 'canManageTemplates', 'canViewAudit', 'canUpdateAlertSettings', 'canTriggerAlerts',
    'canEditContract', 'canEditOwnContract'],
  manager: ['canUpload', 'canDelete', 'canRestore', 'canRestoreOwn', 'canSubmitForReview', 'canApprove', 'canReject',
    'canActivate', 'canViewAudit', 'canTriggerAlerts', 'canEditContract', 'canEditOwnContract'],
  user: ['canUpload', 'canSubmitForReview', 'canRestoreOwn', 'canEditOwnContract'],
  viewer: [],
};

export function accessForRole(role, { readOnly = false } = {}) {
  const granted = readOnly ? [] : MATRIX[role] || [];
  return normalizeAccess({
    state: readOnly ? 'past_due' : 'paid',
    readOnly,
    message: null,
    capabilities: Object.fromEntries(granted.map((key) => [key, true])),
  });
}
