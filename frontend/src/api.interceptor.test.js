import { isAuthEndpoint, shouldRedirectOnUnauthorized } from './api';

describe('401 handling', () => {
  test.each(['/auth/login', '/auth/register', '/auth/forgot-password', '/auth/reset-password', '/api/v1/auth/login', 'https://x.test/api/v1/auth/login/'])(
    '%s is an auth endpoint',
    (url) => expect(isAuthEndpoint(url)).toBe(true)
  );

  test.each(['/auth/me', '/auth/logout', '/contracts', '/auth/login-history', undefined])(
    '%s is not an auth endpoint',
    (url) => expect(isAuthEndpoint(url)).toBe(false)
  );

  test('wrong password on login does not trigger the session-expired redirect', () => {
    expect(shouldRedirectOnUnauthorized({ response: { status: 401 }, config: { url: '/auth/login' } })).toBe(false);
  });

  test('a 401 on a normal endpoint still redirects', () => {
    expect(shouldRedirectOnUnauthorized({ response: { status: 401 }, config: { url: '/contracts' } })).toBe(true);
    expect(shouldRedirectOnUnauthorized({ response: { status: 401 }, config: { url: '/auth/me' } })).toBe(true);
  });

  test('non-401 errors never redirect', () => {
    expect(shouldRedirectOnUnauthorized({ response: { status: 403 }, config: { url: '/contracts' } })).toBe(false);
    expect(shouldRedirectOnUnauthorized({ message: 'Network Error' })).toBe(false);
  });
});

describe('401 interceptor and cross-host redirect', () => {
  const ORIGINAL_ENV = process.env;
  const originalLocation = window.location;
  const handler = () => require('./api').default.interceptors.response.handlers[0].rejected;
  function setLocation(host) {
    delete window.location;
    window.location = { host, pathname: '/contracts', search: '', hash: '', origin: `https://${host}`, href: `https://${host}/contracts`, replace: jest.fn() };
  }
  beforeEach(() => {
    process.env = { ...ORIGINAL_ENV, REACT_APP_APP_URL: 'https://app.lexisense.net', REACT_APP_LANDING_URL: 'https://lexisense.net', REACT_APP_AUTH_ON_LANDING: 'false' };
  });
  afterEach(() => { process.env = ORIGINAL_ENV; window.location = originalLocation; });
  const err = { response: { status: 401 }, config: { url: '/auth/me' } };

  test('landing host: clears session but does not reload landing /login', async () => {
    setLocation('lexisense.net');
    localStorage.setItem('token', 't');
    await expect(handler()(err)).rejects.toBe(err);
    expect(localStorage.getItem('token')).toBeNull();
    expect(window.location.href).toBe('https://lexisense.net/contracts');
  });

  test('app host: still redirects to /login', async () => {
    setLocation('app.lexisense.net');
    await expect(handler()(err)).rejects.toBe(err);
    expect(window.location.href).toBe('/login');
  });
});
