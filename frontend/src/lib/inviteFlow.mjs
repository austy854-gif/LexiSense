/**
 * Pure helpers for the /accept-invite page (AcceptInvitePage.js).
 *
 * Kept free of React so the state machine can be unit-tested with both
 * `node --test` (inviteFlow.test.mjs) and Jest (inviteFlow.test.js).
 *
 * Backend contract (backend/routes/team.py):
 *   POST /team/accept-invite/preview {token}
 *     200 {email, organizationName, inviterName, role, expiresAt}
 *     404 unknown/cancelled  400 expired  409 already accepted  429 rate limited
 *   POST /team/accept-invite {token, password, firstName?, lastName?}
 *     200 {message, email}  404 invalid/used  400 expired  422 bad body
 */
import { getErrorInfo, normalizeRole } from './apiHelpers.mjs';

export const MIN_PASSWORD_LENGTH = 8;

/** User-facing copy (sentence case; wording from lexisense-copy). */
export const INVITE_COPY = {
  missingTitle: 'This invite link is incomplete',
  missingBody: 'The link is missing part of its address. Open the invite email again and use the Accept invite button.',
  invalidTitle: "This invite link doesn't work",
  invalidBody: 'It may have been cancelled or already used. Ask your LexiSense admin to send a new invite.',
  expiredTitle: 'This invite has expired',
  expiredBody: 'Invites stay open for 7 days. Ask your LexiSense admin to send a new one.',
  usedTitle: 'This invite has already been used',
  usedBody: 'Your account is set up. Sign in to see your contracts.',
  rateLimitedBody: 'Too many tries in a row. Wait a minute, then try again.',
  errorBody: "We couldn't check this invite. Refresh the page to try again.",
  formTitle: "You're invited",
  passwordsDontMatch: "Those passwords don't match. Try typing them again.",
  passwordTooShort: 'Use at least 8 characters for your password.',
  submit: 'Accept invite',
  submitting: 'Creating your account...',
  successToast: "You're in. Welcome to LexiSense.",
  successSignInToast: 'Your account is ready. Sign in to continue.',
  acceptFailed: "We couldn't create your account. Please try again.",
};

const ROLE_LABELS = { admin: 'Admin', manager: 'Manager', user: 'User', viewer: 'Viewer' };

/** Sentence-case role label (matches the invite email's "Your role"). */
export function roleLabel(role) {
  return ROLE_LABELS[normalizeRole(role)];
}

/** Read the token from a location.search string such as "?token=abc". */
export function readInviteToken(search) {
  const params = new URLSearchParams(search || '');
  return (params.get('token') || '').trim();
}

/**
 * Map an axios error from either invite endpoint to a page state.
 * @returns {'invalid'|'expired'|'used'|'rate_limited'|'validation'|'error'}
 */
export function classifyInviteError(error) {
  const { status } = getErrorInfo(error);
  switch (status) {
    case 404:
      return 'invalid';
    case 400:
      return 'expired';
    case 409:
      return 'used';
    case 429:
      return 'rate_limited';
    case 422:
      return 'validation';
    default:
      return 'error';
  }
}

function sameEmail(a, b) {
  return typeof a === 'string' && typeof b === 'string' && a.trim().toLowerCase() === b.trim().toLowerCase();
}

/**
 * Decide what the page shows.
 *
 * @param {object} s
 * @param {string} s.token          token from the URL ('' when missing)
 * @param {boolean} s.authLoading   AuthContext still restoring the session
 * @param {object|null} s.user      signed-in user, if any
 * @param {object|null} s.preview   preview response body
 * @param {string|null} s.errorKind result of classifyInviteError
 * @returns {'missing'|'loading'|'invalid'|'expired'|'used'|'rate_limited'|'error'
 *           |'signed_in_same'|'signed_in_other'|'form'}
 */
export function inviteViewState({ token, authLoading, user, preview, errorKind }) {
  if (!token) return 'missing';
  if (errorKind && errorKind !== 'validation') return errorKind;
  if (authLoading || !preview) return 'loading';
  if (user) return sameEmail(user.email, preview.email) ? 'signed_in_same' : 'signed_in_other';
  return 'form';
}

/** Client-side password checks; returns a message or null when valid. */
export function validateInvitePassword(password, confirmPassword) {
  if ((password || '').length < MIN_PASSWORD_LENGTH) return INVITE_COPY.passwordTooShort;
  if (password !== confirmPassword) return INVITE_COPY.passwordsDontMatch;
  return null;
}

/** Body for POST /team/accept-invite (blank names are omitted). */
export function buildAcceptPayload(token, { password, firstName, lastName }) {
  const body = { token, password };
  if (firstName && firstName.trim()) body.firstName = firstName.trim();
  if (lastName && lastName.trim()) body.lastName = lastName.trim();
  return body;
}
