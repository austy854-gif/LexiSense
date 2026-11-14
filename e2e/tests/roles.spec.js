const { test, expect } = require('@playwright/test');
const { USERS, CONTRACT_TEXT, resetData, signInAs, sessionFor, apiUpload } = require('../fixtures/helpers');

test.beforeEach(async ({ request }) => {
  await resetData(request);
});

test('a viewer can read contracts but cannot upload or run workflow actions', async ({ page, request }) => {
  const contract = await apiUpload(request, USERS.user, 'Viewer can read this');

  await signInAs(page, USERS.viewer);
  await page.goto('/contracts');
  await expect(page.getByRole('cell', { name: /Viewer can read this/ }).first()).toBeVisible();
  await expect(page.getByTestId('upload-contract-btn')).toHaveCount(0);
  await expect(page.getByTestId('bulk-upload-btn')).toHaveCount(0);

  await page.goto(`/contracts/${contract.id}`);
  await expect(page.getByTestId('contract-detail-page')).toBeVisible();
  await expect(page.getByTestId('workflow-actions').getByRole('button')).toHaveCount(0);
  // Viewers can see who is on the team but cannot invite anyone.
  await page.getByTestId('nav-team').click();
  await expect(page.getByText('admin@acme.example.com').first()).toBeVisible();
  await expect(page.getByTestId('invite-member-btn')).toHaveCount(0);
});

test('the API refuses a viewer upload with 403 even if the UI is bypassed', async ({ request }) => {
  const { access_token: token } = await sessionFor(request, USERS.viewer);
  const res = await request.post('/api/v1/contracts', {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      title: 'Sneaky',
      file: { name: 'x.txt', mimeType: 'text/plain', buffer: Buffer.from(CONTRACT_TEXT) },
    },
  });
  expect(res.status()).toBe(403);
  expect((await res.json()).detail).toMatch(/Viewers cannot modify contracts/);
});
