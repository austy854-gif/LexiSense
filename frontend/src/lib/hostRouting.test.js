import { appUrl, authRedirectTarget, backendBaseUrl, getHostRole, hostOf } from './hostRouting';

const env = {
  REACT_APP_APP_URL: 'https://app.lexisense.net/',
  REACT_APP_LANDING_URL: 'https://lexisense.net',
};
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
  expect(authRedirectTarget(loc('lexisense.net', '/login'), { REACT_APP_LANDING_URL: 'https://lexisense.net' })).toBeNull();
});

test('backendBaseUrl', () => {
  expect(backendBaseUrl({ REACT_APP_BACKEND_URL: 'https://api.lexisense.net/' }, loc('app.lexisense.net')))
    .toBe('https://api.lexisense.net');
  expect(backendBaseUrl({}, loc('localhost:3000'))).toBe('https://localhost:3000');
});
