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
