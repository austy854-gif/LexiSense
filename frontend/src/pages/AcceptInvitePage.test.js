import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import AcceptInvitePage from './AcceptInvitePage';
import { AuthProvider } from '../contexts/AuthContext';
import { teamAPI, authAPI } from '../api';
import { toast } from 'sonner';

jest.mock('../api', () => ({
  __esModule: true,
  default: {},
  teamAPI: { previewInvite: jest.fn(), acceptInvite: jest.fn() },
  authAPI: { login: jest.fn(), getMe: jest.fn() },
}));
jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const PREVIEW = {
  email: 'new@example.com',
  organizationName: 'Acme Studio',
  inviterName: 'Ada Lovelace',
  role: 'manager',
  expiresAt: '2099-01-01T00:00:00+00:00',
};
const httpError = (status) => Object.assign(new Error('x'), { response: { status, data: { detail: 'x' } } });
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

let container;
let root;

async function renderAt(search) {
  window.history.pushState(null, '', `/accept-invite${search}`);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <AuthProvider>
        <MemoryRouter initialEntries={[`/accept-invite${search}`]}>
          <Routes>
            <Route path="/accept-invite" element={<AcceptInvitePage />} />
            <Route path="/" element={<div data-testid="dashboard">dashboard</div>} />
            <Route path="/login" element={<div data-testid="login">login</div>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>,
    );
  });
  await flush();
}

const q = (id) => container.querySelector(`[data-testid="${id}"]`);

function type(input, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

test('missing token shows the incomplete-link state without calling the API', async () => {
  await renderAt('');
  expect(q('accept-invite-missing')).not.toBeNull();
  expect(teamAPI.previewInvite).not.toHaveBeenCalled();
});

test.each([
  [404, 'accept-invite-invalid'],
  [400, 'accept-invite-expired'],
  [409, 'accept-invite-used'],
])('preview %s shows %s', async (status, testId) => {
  teamAPI.previewInvite.mockRejectedValue(httpError(status));
  await renderAt('?token=tok');
  expect(q(testId)).not.toBeNull();
});

test('logged out: shows the form, strips the token from the URL, and the invite details', async () => {
  teamAPI.previewInvite.mockResolvedValue({ data: PREVIEW });
  await renderAt('?token=tok-123');
  expect(teamAPI.previewInvite).toHaveBeenCalledWith('tok-123');
  expect(window.location.search).toBe('');
  expect(q('accept-invite-form-title').textContent).toBe("You're invited");
  expect(q('accept-invite-email').value).toBe('new@example.com');
  expect(container.textContent).toContain('Acme Studio');
  expect(container.textContent).toContain('Manager');
  expect(q('accept-invite-submit').textContent).toBe('Accept invite');
});

test('submitting accepts the invite, signs in and redirects to the dashboard', async () => {
  teamAPI.previewInvite.mockResolvedValue({ data: PREVIEW });
  teamAPI.acceptInvite.mockResolvedValue({ data: { message: 'ok', email: 'new@example.com' } });
  authAPI.login.mockResolvedValue({
    data: { access_token: 'jwt', user: { id: 'u1', email: 'new@example.com', role: 'manager' } },
  });
  await renderAt('?token=tok-123');

  await act(async () => {
    type(q('accept-invite-password'), 'Sup3rSecret!');
    type(q('accept-invite-confirm-password'), 'Sup3rSecret!');
  });
  await act(async () => {
    q('accept-invite-submit').closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  await flush();

  expect(teamAPI.acceptInvite).toHaveBeenCalledWith({ token: 'tok-123', password: 'Sup3rSecret!' });
  expect(authAPI.login).toHaveBeenCalledWith('new@example.com', 'Sup3rSecret!');
  expect(q('dashboard')).not.toBeNull();
});

test('mismatched passwords never reach the API', async () => {
  teamAPI.previewInvite.mockResolvedValue({ data: PREVIEW });
  await renderAt('?token=tok-123');
  await act(async () => {
    type(q('accept-invite-password'), 'Sup3rSecret!');
    type(q('accept-invite-confirm-password'), 'different!!');
  });
  await act(async () => {
    q('accept-invite-submit').closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  expect(teamAPI.acceptInvite).not.toHaveBeenCalled();
});

test('an invite that expires between preview and submit switches to the expired state', async () => {
  teamAPI.previewInvite.mockResolvedValue({ data: PREVIEW });
  teamAPI.acceptInvite.mockRejectedValue(httpError(400));
  await renderAt('?token=tok-123');
  await act(async () => {
    type(q('accept-invite-password'), 'Sup3rSecret!');
    type(q('accept-invite-confirm-password'), 'Sup3rSecret!');
  });
  await act(async () => {
    q('accept-invite-submit').closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  await flush();
  expect(q('accept-invite-expired')).not.toBeNull();
  expect(authAPI.login).not.toHaveBeenCalled();
});

test('signed in as someone else: offers to sign out instead of showing the form', async () => {
  localStorage.setItem('token', 'jwt');
  localStorage.setItem('user', JSON.stringify({ id: 'a', email: 'boss@example.com', role: 'admin' }));
  authAPI.getMe.mockResolvedValue({ data: { id: 'a', email: 'boss@example.com', role: 'admin' } });
  teamAPI.previewInvite.mockResolvedValue({ data: PREVIEW });
  await renderAt('?token=tok-123');
  expect(q('accept-invite-signed_in_other')).not.toBeNull();
  expect(q('accept-invite-password')).toBeNull();

  await act(async () => { q('accept-invite-sign-out').click(); });
  expect(q('accept-invite-password')).not.toBeNull();
  expect(localStorage.getItem('token')).toBeNull();
});

test('signed in as the invitee: shows the password form so they can join', async () => {
  localStorage.setItem('token', 'jwt');
  localStorage.setItem('user', JSON.stringify({ id: 'n', email: 'new@example.com', role: 'admin' }));
  authAPI.getMe.mockResolvedValue({ data: { id: 'n', email: 'new@example.com', role: 'admin' } });
  teamAPI.previewInvite.mockResolvedValue({ data: { ...PREVIEW, accountExists: true } });
  await renderAt('?token=tok-123');
  expect(q('accept-invite-existing-account')).not.toBeNull();
  expect(q('accept-invite-password')).not.toBeNull();
});

async function submit(password) {
  await act(async () => { type(q('accept-invite-password'), password); });
  await act(async () => {
    q('accept-invite-submit').closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  await flush();
}

test('existing account: asks only for the current password, joins and signs in', async () => {
  teamAPI.previewInvite.mockResolvedValue({ data: { ...PREVIEW, accountExists: true } });
  teamAPI.acceptInvite.mockResolvedValue({ data: { email: 'new@example.com', existingAccount: true, alreadyMember: false } });
  authAPI.login.mockResolvedValue({
    data: { access_token: 'jwt', user: { id: 'u1', email: 'new@example.com', role: 'manager' } },
  });
  await renderAt('?token=tok-123');
  expect(q('accept-invite-confirm-password')).toBeNull();
  expect(container.querySelector('#invite-first-name')).toBeNull();

  await submit('old-pass');

  expect(teamAPI.acceptInvite).toHaveBeenCalledWith({ token: 'tok-123', password: 'old-pass' });
  expect(authAPI.login).toHaveBeenCalledWith('new@example.com', 'old-pass');
  expect(toast.success).toHaveBeenCalledWith("You're in. Welcome to the team.");
  expect(q('dashboard')).not.toBeNull();
});

test('existing account: wrong password keeps the form and shows the server message', async () => {
  teamAPI.previewInvite.mockResolvedValue({ data: { ...PREVIEW, accountExists: true } });
  teamAPI.acceptInvite.mockRejectedValue(
    Object.assign(new Error('x'), { response: { status: 403, data: { detail: "That password doesn't match your LexiSense account." } } }),
  );
  await renderAt('?token=tok-123');
  await submit('wrong-pass');
  expect(toast.error).toHaveBeenCalledWith("That password doesn't match your LexiSense account.");
  expect(q('accept-invite-password')).not.toBeNull();
  expect(authAPI.login).not.toHaveBeenCalled();
});

test('sole-admin refusal (409 on accept) shows the message instead of "already used"', async () => {
  teamAPI.previewInvite.mockResolvedValue({ data: { ...PREVIEW, accountExists: true } });
  teamAPI.acceptInvite.mockRejectedValue(
    Object.assign(new Error('x'), { response: { status: 409, data: { detail: "You're the only admin of your current team." } } }),
  );
  await renderAt('?token=tok-123');
  await submit('old-pass');
  expect(toast.error).toHaveBeenCalledWith("You're the only admin of your current team.");
  expect(q('accept-invite-used')).toBeNull();
});

test('existing account in another team: warns about losing access before the password step', async () => {
  teamAPI.previewInvite.mockResolvedValue({ data: { ...PREVIEW, accountExists: true, accountInOtherTeam: true } });
  await renderAt('?token=tok-123');
  const warning = q('accept-invite-leave-team-warning');
  expect(warning.textContent).toBe(
    "Joining this team moves your account out of your current team. You'll lose access to its contracts, which stay with that team.",
  );
  // Shown before the password field.
  const password = q('accept-invite-password');
  expect(warning.compareDocumentPosition(password) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test.each([
  ['new account', { accountExists: false, accountInOtherTeam: false }],
  ['already in this team', { accountExists: true, accountInOtherTeam: false }],
])('no leave-team warning for %s', async (_label, flags) => {
  teamAPI.previewInvite.mockResolvedValue({ data: { ...PREVIEW, ...flags } });
  await renderAt('?token=tok-123');
  expect(q('accept-invite-leave-team-warning')).toBeNull();
});
