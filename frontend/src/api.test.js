import api from './api';

test('uses a same-origin API base URL when REACT_APP_BACKEND_URL is unset', () => {
  expect(api.defaults.baseURL).toBe('/api/v1');
});
