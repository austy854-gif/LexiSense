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
    process.env = { ...ORIGINAL_ENV, REACT_APP_APP_URL: 'https://app.lexisense.net', REACT_APP_LANDING_URL: 'https://lexisense.net' };
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

describe('401 interceptor with a newer session (real axios pipeline)', () => {
  const originalLocation = window.location;
  let api;
  let originalAdapter;
  beforeEach(() => {
    jest.resetModules();
    api = require('./api').default;
    originalAdapter = api.defaults.adapter;
    delete window.location;
    window.location = { host: 'app.lexisense.net', pathname: '/login', search: '', hash: '', origin: 'https://app.lexisense.net', href: 'https://app.lexisense.net/login', replace: jest.fn() };
    localStorage.clear();
  });
  afterEach(() => { api.defaults.adapter = originalAdapter; window.location = originalLocation; localStorage.clear(); });

  function reject401(onSend) {
    const { AxiosError } = require('axios');
    return (config) => {
      if (onSend) onSend(config);
      return Promise.reject(new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, null, { status: 401, config, data: {}, headers: {} }));
    };
  }

  test('late 401 for an old token does not clear a newer session or redirect', async () => {
    localStorage.setItem('token', 'old-tok');
    localStorage.setItem('user', '{"id":"old"}');
    api.defaults.adapter = reject401((config) => {
      expect(config.headers.Authorization).toBe('Bearer old-tok');
      localStorage.setItem('token', 'new-tok');
      localStorage.setItem('user', '{"id":"new"}');
    });
    await expect(api.get('/auth/me')).rejects.toMatchObject({ response: { status: 401 } });
    expect(localStorage.getItem('token')).toBe('new-tok');
    expect(localStorage.getItem('user')).toBe('{"id":"new"}');
    expect(window.location.href).toBe('https://app.lexisense.net/login');
  });

  test('401 for the current token still clears and redirects', async () => {
    localStorage.setItem('token', 'tok');
    localStorage.setItem('user', '{"id":"u"}');
    api.defaults.adapter = reject401();
    await expect(api.get('/auth/me')).rejects.toMatchObject({ response: { status: 401 } });
    expect(localStorage.getItem('token')).toBeNull();
    expect(localStorage.getItem('user')).toBeNull();
    expect(window.location.href).toBe('/login');
  });
});
