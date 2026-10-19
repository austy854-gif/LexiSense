/**
 * Billing page helpers (BillingPage.js). No trial lengths or upload limits are
 * shown to users; only the trial's end date, when the backend provides it.
 */

/** Parse an ISO timestamp; null when missing or invalid. */
function parseDate(value) {
  if (!value || typeof value !== 'string') return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** True while the trial end date is known and in the future. */
export function isTrialActive(trialEndDate, now = new Date()) {
  const end = parseDate(trialEndDate);
  return Boolean(end) && end > now;
}

/**
 * "Trial ends October 25, 2026" for a valid date, else null (hide the line).
 * `locale`/`timeZone` default to the browser's; tests pin them.
 */
export function trialEndsLabel(trialEndDate, { locale, timeZone } = {}) {
  const end = parseDate(trialEndDate);
  if (!end) return null;
  const formatted = end.toLocaleDateString(locale, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    ...(timeZone ? { timeZone } : {}),
  });
  return `Trial ends ${formatted}`;
}

/**
 * What the "Free trial" plan card shows (B6). The trial can't be bought or
 * restarted (signup starts it for free), so the card never has a button:
 * - null: hide the card (paid "team" or "cancelled" orgs, or tier unknown)
 * - "Current plan": on the trial and it hasn't ended
 * - "Trial ended": on the trial but the end date has passed or is unknown
 */
export function trialCardStatus(subscriptionTier, trialEndDate, now = new Date()) {
  if (subscriptionTier !== 'trial') return null;
  return isTrialActive(trialEndDate, now) ? 'Current plan' : 'Trial ended';
}
