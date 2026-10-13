import api from './api';

test('API base URL ends with /api/v1 (same-origin /api/v1 when REACT_APP_BACKEND_URL is unset)', () => {
  expect(api.defaults.baseURL).toMatch(/^(https?:\/\/[^/]+)?\/api\/v1$/);
});
