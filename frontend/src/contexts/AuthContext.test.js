import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { AuthProvider, useAuth } from './AuthContext';
import { authAPI } from '../api';

jest.mock('../api', () => ({
  __esModule: true,
  authAPI: {
    login: jest.fn(),
    register: jest.fn(),
    getMe: jest.fn(),
    logout: jest.fn(),
  },
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

let container;
let root;
let latestAuth;

function Probe() {
  latestAuth = useAuth();
  return (
    <div data-testid="probe">
      {latestAuth.loading ? 'loading' : latestAuth.user ? `user:${latestAuth.user.email}` : 'nouser'}
    </div>
  );
}

async function mountProvider() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
  });
  await flush();
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  latestAuth = null;
  authAPI.getMe.mockResolvedValue({ data: { id: 'me', email: 'me@example.com', role: 'admin' } });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

test.each([
  ['undefined'],
  ['null'],
  [''],
  ['{not-json'],
  ['42'],
  ['"just-a-string"'],
  ['[]'],
])('mount with bad stored user %j does not crash and clears storage', async (bad) => {
  localStorage.setItem('token', 'tok');
  localStorage.setItem('user', bad);

  await expect(mountProvider()).resolves.toBeUndefined();

  expect(container.textContent).toBe('nouser');
  expect(localStorage.getItem('user')).toBeNull();
  expect(localStorage.getItem('token')).toBeNull();
  expect(authAPI.getMe).not.toHaveBeenCalled();
});

test('mount with null user key (getItem null) and token does not crash', async () => {
  localStorage.setItem('token', 'tok');
  // no user key → getItem returns null
  await expect(mountProvider()).resolves.toBeUndefined();
  expect(container.textContent).toBe('nouser');
  expect(authAPI.getMe).not.toHaveBeenCalled();
});

test('valid stored user still loads and refreshes via getMe', async () => {
  const stored = { id: 'u1', email: 'stored@example.com', role: 'admin' };
  localStorage.setItem('token', 'tok');
  localStorage.setItem('user', JSON.stringify(stored));
  authAPI.getMe.mockResolvedValue({
    data: { id: 'u1', email: 'fresh@example.com', role: 'admin' },
  });

  await mountProvider();

  expect(container.textContent).toBe('user:fresh@example.com');
  expect(JSON.parse(localStorage.getItem('user'))).toEqual({
    id: 'u1',
    email: 'fresh@example.com',
    role: 'admin',
  });
  expect(authAPI.getMe).toHaveBeenCalled();
});

test('register with TokenResponse shape stores user and token', async () => {
  authAPI.register.mockResolvedValue({
    data: {
      access_token: 'jwt-reg',
      token_type: 'bearer',
      user: { id: 'n1', email: 'new@example.com', role: 'admin' },
    },
  });
  await mountProvider();

  let result;
  await act(async () => {
    result = await latestAuth.register({
      email: 'new@example.com',
      password: 'Sup3rSecret!',
    });
  });

  expect(result).toEqual({ id: 'n1', email: 'new@example.com', role: 'admin' });
  expect(localStorage.getItem('token')).toBe('jwt-reg');
  expect(JSON.parse(localStorage.getItem('user'))).toEqual({
    id: 'n1',
    email: 'new@example.com',
    role: 'admin',
  });
  expect(localStorage.getItem('user')).not.toBe('undefined');
  expect(container.textContent).toBe('user:new@example.com');
});

test("register without user never stores the string 'undefined'", async () => {
  authAPI.register.mockResolvedValue({
    data: {
      access_token: 'jwt-only',
      token_type: 'bearer',
      // user missing — the shape that previously wrote "undefined"
    },
  });
  await mountProvider();

  await act(async () => {
    await latestAuth.register({ email: 'x@example.com', password: 'Sup3rSecret!' });
  });

  expect(localStorage.getItem('user')).toBeNull();
  expect(localStorage.getItem('user')).not.toBe('undefined');
  expect(localStorage.getItem('token')).toBe('jwt-only');
  expect(container.textContent).toBe('nouser');
});

test('login without user never stores the string undefined for user', async () => {
  authAPI.login.mockResolvedValue({
    data: { access_token: 'jwt-login' },
  });
  await mountProvider();

  await act(async () => {
    await latestAuth.login('a@example.com', 'pw');
  });

  expect(localStorage.getItem('user')).toBeNull();
  expect(localStorage.getItem('token')).toBe('jwt-login');
});

test('login with full TokenResponse stores user', async () => {
  authAPI.login.mockResolvedValue({
    data: {
      access_token: 'jwt-ok',
      user: { id: 'u2', email: 'ok@example.com', role: 'manager' },
    },
  });
  await mountProvider();

  await act(async () => {
    await latestAuth.login('ok@example.com', 'pw');
  });

  expect(JSON.parse(localStorage.getItem('user')).email).toBe('ok@example.com');
  expect(localStorage.getItem('token')).toBe('jwt-ok');
});

function deferred() {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test('mount does not crash when localStorage access throws', async () => {
  const spy = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied'); });
  try {
    await expect(mountProvider()).resolves.toBeUndefined();
    expect(container.textContent).toBe('nouser');
    expect(authAPI.getMe).not.toHaveBeenCalled();
  } finally {
    spy.mockRestore();
  }
});

test('late startup getMe success does not overwrite a newer login', async () => {
  localStorage.setItem('token', 'old-tok');
  localStorage.setItem('user', JSON.stringify({ id: 'old', email: 'old@example.com' }));
  const d = deferred();
  authAPI.getMe.mockReturnValue(d.promise);
  authAPI.login.mockResolvedValue({ data: { access_token: 'new-tok', user: { id: 'new', email: 'new@example.com' } } });
  await mountProvider();
  await act(async () => { await latestAuth.login('new@example.com', 'pw'); });
  await act(async () => { d.resolve({ data: { id: 'old', email: 'old@example.com' } }); });
  await flush();
  expect(container.textContent).toBe('user:new@example.com');
  expect(localStorage.getItem('token')).toBe('new-tok');
  expect(JSON.parse(localStorage.getItem('user')).id).toBe('new');
});

test('late startup getMe failure does not clear a newer login', async () => {
  localStorage.setItem('token', 'old-tok');
  localStorage.setItem('user', JSON.stringify({ id: 'old', email: 'old@example.com' }));
  const d = deferred();
  authAPI.getMe.mockReturnValue(d.promise);
  authAPI.login.mockResolvedValue({ data: { access_token: 'new-tok', user: { id: 'new', email: 'new@example.com' } } });
  await mountProvider();
  await act(async () => { await latestAuth.login('new@example.com', 'pw'); });
  await act(async () => { d.reject({ response: { status: 500 } }); });
  await flush();
  expect(container.textContent).toBe('user:new@example.com');
  expect(localStorage.getItem('token')).toBe('new-tok');
});

test('late startup getMe success does not resurrect a session after logout', async () => {
  localStorage.setItem('token', 'old-tok');
  localStorage.setItem('user', JSON.stringify({ id: 'old', email: 'old@example.com' }));
  const d = deferred();
  authAPI.getMe.mockReturnValue(d.promise);
  authAPI.logout.mockResolvedValue({});
  await mountProvider();
  await act(async () => { await latestAuth.logout(); });
  await act(async () => { d.resolve({ data: { id: 'old', email: 'old@example.com' } }); });
  await flush();
  expect(container.textContent).toBe('nouser');
  expect(localStorage.getItem('token')).toBeNull();
});
