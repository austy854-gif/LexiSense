import {
  GENERIC_ERROR_MESSAGE,
  NETWORK_ERROR_MESSAGE,
  TOO_LARGE_MESSAGE,
  getErrorInfo,
} from './apiHelpers.mjs';

describe('getErrorInfo never leaks technical text', () => {
  test('axios network error (no response) maps to a friendly message', () => {
    const err = { isAxiosError: true, code: 'ERR_NETWORK', message: 'Network Error', request: {} };
    expect(getErrorInfo(err).message).toBe(NETWORK_ERROR_MESSAGE);
  });

  test('timeout (no response) maps to the network message', () => {
    const err = { isAxiosError: true, code: 'ECONNABORTED', message: 'timeout of 0ms exceeded', request: {} };
    expect(getErrorInfo(err).message).toBe(NETWORK_ERROR_MESSAGE);
  });

  test('413 with a proxy HTML body maps to the too-large message', () => {
    const err = {
      message: 'Request failed with status code 413',
      response: { status: 413, data: '<html><head><title>413 Request Entity Too Large</title></head></html>' },
    };
    const info = getErrorInfo(err);
    expect(info.message).toBe(TOO_LARGE_MESSAGE);
    expect(info.status).toBe(413);
  });

  test('502 with an HTML body falls back to the generic message', () => {
    const err = {
      message: 'Request failed with status code 502',
      response: { status: 502, data: '<!DOCTYPE html><html><body>Bad Gateway</body></html>' },
    };
    const info = getErrorInfo(err);
    expect(info.message).toBe(GENERIC_ERROR_MESSAGE);
    expect(info.message).not.toMatch(/<|status code/);
  });

  test('a response with no usable body never shows axios "status code" text', () => {
    const err = { message: 'Request failed with status code 500', response: { status: 500, data: '' } };
    expect(getErrorInfo(err).message).toBe(GENERIC_ERROR_MESSAGE);
  });

  test('server envelope and legacy detail are still preferred', () => {
    expect(
      getErrorInfo({ response: { status: 403, data: { error: { code: 'trial', message: 'Your free trial has ended.' } } } })
        .message
    ).toBe('Your free trial has ended.');
    expect(getErrorInfo({ response: { status: 401, data: { detail: 'Invalid email or password' } } }).message).toBe(
      'Invalid email or password'
    );
  });

  test('a plain-text 5xx body is not shown', () => {
    expect(getErrorInfo({ response: { status: 500, data: 'Internal Server Error' } }).message).toBe(GENERIC_ERROR_MESSAGE);
  });

  test('a short plain-text body is still shown', () => {
    expect(getErrorInfo({ response: { status: 400, data: 'Title is required' } }).message).toBe('Title is required');
  });

  test('a non-axios thrown error does not leak its message', () => {
    expect(getErrorInfo(new TypeError("Cannot read properties of undefined (reading 'id')")).message).toBe(
      GENERIC_ERROR_MESSAGE
    );
  });
});
