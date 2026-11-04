// Host-aware routing helpers for the domain split:
//   landing: lexisense.net / www.lexisense.net (REACT_APP_LANDING_URL)
//   app:     app.lexisense.net                 (REACT_APP_APP_URL)
// With the env vars unset (local dev, previews) everything behaves as a
// single combined host, exactly as before the split.

const trimSlash = (url) => (url || '').trim().replace(/\/+$/, '');

export function hostOf(url) {
  const clean = trimSlash(url);
  if (!clean) return '';
  try {
    return new URL(clean).host.toLowerCase();
  } catch {
    return '';
  }
}

// Strips a leading "www." so lexisense.net and www.lexisense.net match.
const bare = (host) => host.replace(/^www\./, '');

function readEnv(env) {
  return {
    appUrl: trimSlash(env.REACT_APP_APP_URL),
    landingUrl: trimSlash(env.REACT_APP_LANDING_URL),
  };
}

function currentHost(location) {
  return ((location && location.host) || '').toLowerCase();
}

/** 'app' | 'landing' | 'combined' */
export function getHostRole(env = process.env, location = window.location) {
  const { appUrl, landingUrl } = readEnv(env);
  const host = currentHost(location);
  const appHost = hostOf(appUrl);
  const landingHost = hostOf(landingUrl);
  if (!host) return 'combined';
  if (appHost && host === appHost) return 'app';
  if (landingHost && bare(host) === bare(landingHost)) return 'landing';
  return 'combined';
}

/** Absolute app URL for a path when REACT_APP_APP_URL is set, else the relative path. */
export function appUrl(path, env = process.env) {
  const { appUrl: base } = readEnv(env);
  return base ? `${base}${path}` : path;
}

/**
 * Where a landing-host visit to an auth page (/login, /register) must go:
 * the same path (plus query/hash) on the app host. Returns null when no
 * cross-host redirect applies.
 */
export function authRedirectTarget(location = window.location, env = process.env) {
  if (getHostRole(env, location) !== 'landing') return null;
  const { appUrl: base } = readEnv(env);
  if (!base || hostOf(base) === currentHost(location)) return null;
  return `${base}${location.pathname || ''}${location.search || ''}${location.hash || ''}`;
}

/** Public base URL for the API (webhooks etc.), falling back to the page origin. */
export function backendBaseUrl(env = process.env, location = window.location) {
  return trimSlash(env.REACT_APP_BACKEND_URL) || trimSlash(location && location.origin);
}
