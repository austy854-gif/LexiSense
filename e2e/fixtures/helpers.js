// Shared helpers: reset the fixture data and sign in without the login form.
const { expect } = require('@playwright/test');

const PASSWORD = 'E2e-Passw0rd!';

const USERS = {
  admin: 'admin@acme.example.com',
  manager: 'manager@acme.example.com',
  user: 'user@acme.example.com',
  viewer: 'viewer@acme.example.com',
  overdueAdmin: 'admin@overdue.example.com',
};

async function resetData(request) {
  const res = await request.post('/__e2e/reset');
  expect(res.ok()).toBeTruthy();
}

/** Mint a session for a fixture user (bypasses the per-IP login limit). */
async function sessionFor(request, email) {
  const res = await request.post('/__e2e/session', { data: { email } });
  expect(res.ok()).toBeTruthy();
  return res.json();
}

/** Open the app already signed in as ``email``. */
async function signInAs(page, email) {
  const { access_token: token, user } = await sessionFor(page.request, email);
  await page.addInitScript(
    ([t, u]) => {
      if (!window.sessionStorage.getItem('e2e-seeded')) {
        window.localStorage.setItem('token', t);
        window.localStorage.setItem('user', JSON.stringify(u));
        window.sessionStorage.setItem('e2e-seeded', '1');
      }
    },
    [token, user],
  );
  return token;
}

/** Upload a text contract through the API as ``email``; returns the contract. */
async function apiUpload(request, email, title) {
  const { access_token: token } = await sessionFor(request, email);
  const res = await request.post('/api/v1/contracts', {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      title,
      contractType: 'Service Agreement',
      counterparty: 'Northwind Traders',
      file: { name: 'msa.txt', mimeType: 'text/plain', buffer: Buffer.from(CONTRACT_TEXT) },
    },
  });
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

const CONTRACT_TEXT =
  'MASTER SERVICES AGREEMENT between Acme Legal Inc. and Northwind Traders Ltd. ' +
  'Term: 12 months from 1 January 2027. Fees: USD 120,000. Payment: net 30.';

module.exports = { PASSWORD, USERS, resetData, sessionFor, signInAs, apiUpload, CONTRACT_TEXT };
