import {
  CAPABILITY_KEYS,
  FAILED_ACCESS,
  LOADING_ACCESS,
  hasCapability,
  hasContractCapability,
  normalizeAccess,
} from './access.mjs';

const allTrue = Object.fromEntries(CAPABILITY_KEYS.map((k) => [k, true]));

describe('workspace access fails closed', () => {
  test.each(CAPABILITY_KEYS)('%s is false while capabilities are loading', (key) => {
    expect(hasCapability(LOADING_ACCESS, key)).toBe(false);
  });

  test.each(CAPABILITY_KEYS)('%s is false when the capabilities request failed', (key) => {
    expect(hasCapability(FAILED_ACCESS, key)).toBe(false);
  });

  test('missing, empty or malformed access grants nothing', () => {
    expect(hasCapability(undefined, 'canUpload')).toBe(false);
    expect(hasCapability(null, 'canUpload')).toBe(false);
    expect(hasCapability({}, 'canUpload')).toBe(false);
    expect(hasCapability({ capabilities: allTrue }, 'canUpload')).toBe(false); // never marked ready
    expect(normalizeAccess(undefined)).toBe(FAILED_ACCESS);
    expect(normalizeAccess({ state: 'paid' })).toBe(FAILED_ACCESS);
  });

  test('an empty capabilities object grants nothing (no role fallback)', () => {
    const access = normalizeAccess({ state: 'paid', readOnly: false, capabilities: {} });
    expect(access.ready).toBe(true);
    for (const key of CAPABILITY_KEYS) expect(hasCapability(access, key)).toBe(false);
  });

  test('only an explicit boolean true grants a capability', () => {
    const access = normalizeAccess({
      state: 'paid', readOnly: false,
      capabilities: { canUpload: true, canDelete: 'true', canApprove: 1, canReject: null },
    });
    expect(hasCapability(access, 'canUpload')).toBe(true);
    expect(hasCapability(access, 'canDelete')).toBe(false);
    expect(hasCapability(access, 'canApprove')).toBe(false);
    expect(hasCapability(access, 'canReject')).toBe(false);
  });

  test('a read-only workspace blocks writes even if a flag says true, but keeps audit view', () => {
    const access = normalizeAccess({ state: 'past_due', readOnly: true, capabilities: allTrue });
    expect(hasCapability(access, 'canUpload')).toBe(false);
    expect(hasCapability(access, 'canApprove')).toBe(false);
    expect(hasCapability(access, 'canViewAudit')).toBe(true);
  });
});

describe('own-contract capabilities', () => {
  const userAccess = normalizeAccess({ state: 'paid', capabilities: { canRestoreOwn: true } });
  const me = { id: 'u1' };

  test('a user may restore a contract they uploaded', () => {
    expect(hasContractCapability(userAccess, 'canRestore', 'canRestoreOwn', { uploadedBy: 'u1' }, me)).toBe(true);
  });

  test('a user may not restore someone else\'s contract', () => {
    expect(hasContractCapability(userAccess, 'canRestore', 'canRestoreOwn', { uploadedBy: 'u2' }, me)).toBe(false);
  });

  test('missing contract or user fails closed', () => {
    expect(hasContractCapability(userAccess, 'canRestore', 'canRestoreOwn', null, me)).toBe(false);
    expect(hasContractCapability(userAccess, 'canRestore', 'canRestoreOwn', { uploadedBy: undefined }, {})).toBe(false);
  });

  test('org-wide capability allows any contract', () => {
    const managerAccess = normalizeAccess({ state: 'paid', capabilities: { canRestore: true } });
    expect(hasContractCapability(managerAccess, 'canRestore', 'canRestoreOwn', { uploadedBy: 'u2' }, me)).toBe(true);
  });

  test('loading access denies even the uploader', () => {
    expect(hasContractCapability(LOADING_ACCESS, 'canRestore', 'canRestoreOwn', { uploadedBy: 'u1' }, me)).toBe(false);
  });
});
