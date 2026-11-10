import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ResetPasswordPage from './ResetPasswordPage';

jest.mock('../api', () => ({ authAPI: { resetPassword: jest.fn() } }));
jest.mock('../components/AuthShell', () => ({ AuthShell: ({ children }) => <div>{children}</div> }));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

test('a bad reset token shows a friendly toast, not "Request validation failed" (LX-ENG-TOAST-01)', async () => {
  const { authAPI } = require('../api');
  authAPI.resetPassword.mockRejectedValue({
    response: {
      status: 422,
      data: {
        error: {
          code: 'validation_error',
          message: 'Request validation failed',
          details: { fields: [{ field: 'token', message: 'String should have at least 20 characters', type: 'string_too_short' }] },
        },
        detail: 'Request validation failed',
      },
    },
  });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/reset-password?token=bad']}>
        <ResetPasswordPage />
      </MemoryRouter>,
    );
  });
  const input = container.querySelector('[data-testid="reset-password"]');
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  await act(async () => {
    setValue.call(input, 'longenough123');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => {
    container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  const { toast } = require('sonner');
  expect(authAPI.resetPassword).toHaveBeenCalledWith('bad', 'longenough123');
  expect(toast.error).toHaveBeenCalledWith('This link is invalid or has expired. Request a new one.');
  expect(toast.error.mock.calls.flat().join(' ')).not.toMatch(/validation failed/i);
  act(() => root.unmount());
  container.remove();
});
