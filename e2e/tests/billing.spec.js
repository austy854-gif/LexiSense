const { test, expect } = require('@playwright/test');
const { USERS, CONTRACT_TEXT, resetData, signInAs, sessionFor } = require('../fixtures/helpers');

test.beforeEach(async ({ request }) => {
  await resetData(request);
});

test('a past-due organization is read-only in the UI', async ({ page }) => {
  await signInAs(page, USERS.overdueAdmin);
  await page.goto('/contracts');
  await expect(page.getByRole('cell', { name: /Overdue LLC Lease/ }).first()).toBeVisible();
  await expect(page.getByTestId('upload-contract-btn')).toHaveCount(0);
  await expect(page.getByTestId('upload-access-message')).toBeVisible();

  await page.goto('/billing');
  await expect(page.getByTestId('billing-page')).toBeVisible();
  await expect(page.getByTestId('billing-readonly')).toBeVisible();
});

test('the billing portal button hands off to Stripe', async ({ page }) => {
  // Stripe is not configured in the e2e stack, so both the API call that
  // creates the portal session and Stripe's own page are stubbed here.
  await page.route('**/api/v1/billing/portal', (route) =>
    route.fulfill({ json: { url: 'https://billing.stripe.com/p/session/e2e' } }),
  );
  await page.route('https://billing.stripe.com/**', (route) =>
    route.fulfill({ contentType: 'text/html', body: '<h1>Stripe billing portal (stub)</h1>' }),
  );
  await signInAs(page, USERS.overdueAdmin);
  await page.goto('/billing');
  await page.getByTestId('billing-primary-action').getByRole('button').first().click();
  await expect(page).toHaveURL('https://billing.stripe.com/p/session/e2e');
  await expect(page.getByRole('heading', { name: /Stripe billing portal/ })).toBeVisible();
});

test('a past-due organization gets 403 for writes but keeps its session', async ({ page, request }) => {
  const { access_token: token } = await sessionFor(request, USERS.overdueAdmin);
  const upload = await request.post('/api/v1/contracts', {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      title: 'Blocked',
      file: { name: 'x.txt', mimeType: 'text/plain', buffer: Buffer.from(CONTRACT_TEXT) },
    },
  });
  expect(upload.status()).toBe(403);
  const invite = await request.post('/api/v1/team/invite', {
    headers: { Authorization: `Bearer ${token}` },
    data: { email: 'new.hire@overdue.example.com', role: 'user' },
  });
  expect(invite.status()).toBe(403);

  // In the UI, a refused write shows the server's reason and does not sign
  // the user out (403 is not 401).
  await signInAs(page, USERS.overdueAdmin);
  await page.goto('/team');
  await expect(page.getByTestId('team-page')).toBeVisible();
  // Admins still see the invite button; the server refuses the write.
  await page.getByTestId('invite-member-btn').click();
  await page.getByTestId('invite-email').fill('new.hire@overdue.example.com');
  const refused = page.waitForResponse((r) => r.url().endsWith('/api/v1/team/invite'));
  await page.getByTestId('invite-submit').click();
  expect((await refused).status()).toBe(403);
  await expect(page.getByText(/Your last payment failed, so your account is read-only/)).toBeVisible();
  await expect(page).toHaveURL(/\/team$/);
  await page.goto('/contracts');
  await expect(page.getByTestId('contracts-page')).toBeVisible();
  await expect(page).toHaveURL(/\/contracts$/);
});
