import { appUrl, authOnLandingEnabled, authRedirectTarget, backendBaseUrl, getHostRole, hostOf } from './hostRouting';

const splitEnv = {
  REACT_APP_APP_URL: 'https://app.lexisense.net/',
  REACT_APP_LANDING_URL: 'https://lexisense.net',
  // Permanent domain-split behaviour under test.
  REACT_APP_AUTH_ON_LANDING: 'false',
};
const env = splitEnv;
const loc = (host, pathname = '/', search = '', hash = '') => ({
  host, pathname, search, hash, origin: `https://${host}`,
});

test('hostOf parses and tolerates junk', () => {
  expect(hostOf('https://App.lexisense.net/')).toBe('app.lexisense.net');
  expect(hostOf('')).toBe('');
  expect(hostOf('not a url')).toBe('');
});

test('getHostRole', () => {
  expect(getHostRole(env, loc('app.lexisense.net'))).toBe('app');
  expect(getHostRole(env, loc('lexisense.net'))).toBe('landing');
  expect(getHostRole(env, loc('www.lexisense.net'))).toBe('landing');
  expect(getHostRole(env, loc('localhost:3000'))).toBe('combined');
  expect(getHostRole({}, loc('app.lexisense.net'))).toBe('combined');
});

test('appUrl absolute when set, relative when unset', () => {
  expect(appUrl('/register', env)).toBe('https://app.lexisense.net/register');
  expect(appUrl('/login', {})).toBe('/login');
});

test('authRedirectTarget only on landing host', () => {
  expect(authRedirectTarget(loc('www.lexisense.net', '/register', '?plan=team', '#x'), env))
    .toBe('https://app.lexisense.net/register?plan=team#x');
  expect(authRedirectTarget(loc('app.lexisense.net', '/login'), env)).toBeNull();
  expect(authRedirectTarget(loc('localhost:3000', '/login'), env)).toBeNull();
  expect(authRedirectTarget(loc('lexisense.net', '/login'), {})).toBeNull();
  expect(authRedirectTarget(loc('lexisense.net', '/login'), { REACT_APP_LANDING_URL: 'https://lexisense.net', REACT_APP_AUTH_ON_LANDING: 'false' })).toBeNull();
});

test('backendBaseUrl', () => {
  expect(backendBaseUrl({ REACT_APP_BACKEND_URL: 'https://api.lexisense.net/' }, loc('app.lexisense.net')))
    .toBe('https://api.lexisense.net');
  expect(backendBaseUrl({}, loc('localhost:3000'))).toBe('https://localhost:3000');
});

test('authOnLandingEnabled defaults on; only "false" disables', () => {
  expect(authOnLandingEnabled({})).toBe(true);
  expect(authOnLandingEnabled({ REACT_APP_AUTH_ON_LANDING: 'true' })).toBe(true);
  expect(authOnLandingEnabled({ REACT_APP_AUTH_ON_LANDING: '' })).toBe(true);
  expect(authOnLandingEnabled({ REACT_APP_AUTH_ON_LANDING: 'false' })).toBe(false);
});

test('temp auth-on-landing: landing treated as combined, no redirect, relative CTAs', () => {
  const temp = {
    REACT_APP_APP_URL: 'https://app.lexisense.net/',
    REACT_APP_LANDING_URL: 'https://lexisense.net',
    // flag unset → default temporary behaviour
  };
  expect(getHostRole(temp, loc('lexisense.net'))).toBe('combined');
  expect(getHostRole(temp, loc('www.lexisense.net'))).toBe('combined');
  expect(getHostRole(temp, loc('app.lexisense.net'))).toBe('app');
  expect(authRedirectTarget(loc('www.lexisense.net', '/login', '?next=1'), temp)).toBeNull();
  expect(authRedirectTarget(loc('lexisense.net', '/register'), temp)).toBeNull();
  expect(appUrl('/login', temp)).toBe('/login');
  expect(appUrl('/register', temp)).toBe('/register');
});
