import {
  INVALID_LINK_MESSAGE,
  VALIDATION_ERROR_MESSAGE,
  fieldLabel,
  friendlyFieldMessage,
  friendlyValidationMessage,
  getErrorInfo,
  validationFieldMessages,
} from './apiHelpers.mjs';

// LX-ENG-TOAST-01: the backend's 422 envelope says "Request validation failed";
// users must see field-level copy instead, and never raw pydantic text.

function validationError(fields) {
  return {
    message: 'Request failed with status code 422',
    response: {
      status: 422,
      data: {
        error: { code: 'validation_error', message: 'Request validation failed', details: { fields } },
        detail: 'Request validation failed',
        requestId: 'req-1',
        status: 422,
      },
    },
  };
}

test('reset-password with a bad (short) token shows the broken-link message', () => {
  const info = getErrorInfo(
    validationError([{ field: 'token', message: 'String should have at least 20 characters', type: 'string_too_short' }]),
  );
  expect(info.message).toBe(INVALID_LINK_MESSAGE);
  expect(info.message).not.toMatch(/Request validation failed/);
  expect(info.fieldMessages).toEqual({ token: INVALID_LINK_MESSAGE });
  expect(info.code).toBe('validation_error');
  expect(info.status).toBe(422);
  expect(info.requestId).toBe('req-1');
});

test('bad token plus short password still reads as one broken-link message', () => {
  const info = getErrorInfo(
    validationError([
      { field: 'token', message: 'String should have at least 20 characters', type: 'string_too_short' },
      { field: 'password', message: 'String should have at least 8 characters', type: 'string_too_short' },
    ]),
  );
  expect(info.message).toBe(INVALID_LINK_MESSAGE);
  expect(info.fieldMessages.password).toBe('Password must be at least 8 characters.');
});

test('short password maps to a field message with the bound', () => {
  const info = getErrorInfo(
    validationError([{ field: 'password', message: 'String should have at least 8 characters', type: 'string_too_short' }]),
  );
  expect(info.message).toBe('Password must be at least 8 characters.');
});

test.each([
  [{ field: 'email', message: 'value is not a valid email address: An email address must have an @-sign.', type: 'value_error' }, 'Enter a valid email address.'],
  [{ field: 'email', message: 'Field required', type: 'missing' }, 'Email is required.'],
  [{ field: 'password', message: 'String should have at most 72 characters', type: 'string_too_long' }, 'Password must be at most 72 characters.'],
  [{ field: 'body.fullName', message: 'Field required', type: 'missing' }, 'Full name is required.'],
  [{ field: 'organizationName', message: 'String should have at least 1 character', type: 'string_too_short' }, 'Organization name is required.'],
  [{ field: 'role', message: "Input should be 'admin', 'manager', 'user' or 'viewer'", type: 'literal_error' }, 'Choose a valid role.'],
  [{ field: 'query.limit', message: 'Input should be less than or equal to 200', type: 'less_than_equal' }, 'Check the limit and try again.'],
  [{ field: 'contractValue', message: 'some internal pydantic detail', type: 'float_parsing' }, 'Check the contract value and try again.'],
])('%j → %s', (entry, expected) => {
  expect(friendlyFieldMessage(entry)).toBe(expected);
});

test('raw server text is never echoed', () => {
  const info = getErrorInfo(
    validationError([{ field: 'x_internal_model.secret', message: 'Value error, InternalModel(id=1) failed', type: 'value_error' }]),
  );
  expect(info.message).not.toMatch(/InternalModel|Value error|validation failed/);
});

test('a validation error with no usable fields gets the generic form message', () => {
  expect(getErrorInfo(validationError([])).message).toBe(VALIDATION_ERROR_MESSAGE);
  expect(getErrorInfo(validationError(undefined)).message).toBe(VALIDATION_ERROR_MESSAGE);
  expect(friendlyValidationMessage([null, 'junk', { field: '' }])).toBe(VALIDATION_ERROR_MESSAGE);
});

test('raw FastAPI 422 shape (detail is a list) is mapped too', () => {
  const info = getErrorInfo({
    response: {
      status: 422,
      data: { detail: [{ loc: ['body', 'password'], msg: 'String should have at least 8 characters', type: 'string_too_short' }] },
    },
  });
  expect(info.message).toBe('Password must be at least 8 characters.');
  expect(info.fieldMessages).toEqual({ password: 'Password must be at least 8 characters.' });
});

test('at most two messages are joined into the toast; first per field wins', () => {
  const fields = [
    { field: 'email', type: 'missing' },
    { field: 'email', type: 'value_error' },
    { field: 'password', type: 'missing' },
    { field: 'fullName', type: 'missing' },
  ];
  expect(validationFieldMessages(fields)).toEqual({
    email: 'Email is required.',
    password: 'Password is required.',
    fullName: 'Full name is required.',
  });
  expect(friendlyValidationMessage(fields)).toBe('Email is required. Password is required.');
});

test('non-validation errors keep their server message', () => {
  const info = getErrorInfo({ response: { status: 400, data: { detail: 'Invalid or expired reset link' } } });
  expect(info.message).toBe('Invalid or expired reset link');
  expect(info.fieldMessages).toBeNull();
});

test('fieldLabel humanises paths', () => {
  expect(fieldLabel('body.items.0.dueDate')).toBe('Due date');
  expect(fieldLabel('')).toBe('');
});
