const { test, expect } = require('@playwright/test');
const { USERS, CONTRACT_TEXT, resetData, signInAs } = require('../fixtures/helpers');

test.beforeEach(async ({ request }) => {
  await resetData(request);
});

test('a user uploads a contract and reads its analysis', async ({ page }) => {
  await signInAs(page, USERS.user);
  await page.goto('/contracts');
  await expect(page.getByTestId('contracts-empty')).toBeVisible();

  await page.getByTestId('upload-contract-btn').click();
  await page.getByTestId('upload-title').fill('Northwind MSA');
  await page.getByTestId('upload-counterparty').fill('Northwind Traders');
  await page.getByTestId('upload-file').setInputFiles({
    name: 'northwind-msa.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from(CONTRACT_TEXT),
  });
  const uploaded = page.waitForResponse(
    (r) => r.url().endsWith('/api/v1/contracts') && r.request().method() === 'POST',
  );
  await page.getByTestId('upload-submit').click();
  expect((await uploaded).status()).toBe(200);

  // The new contract is listed; open it from the toast action.
  await expect(page.getByRole('cell', { name: /Northwind MSA/ }).first()).toBeVisible();
  await page.getByRole('button', { name: 'View contract' }).click();

  const detail = page.getByTestId('contract-detail-page');
  await expect(detail).toBeVisible();
  await expect(detail.getByRole('heading', { name: 'Northwind MSA' })).toBeVisible();
  // The stubbed analysis is rendered: summary, a key term and the top risk.
  await expect(detail.getByText(/Master services agreement between Acme Legal/)).toBeVisible();
  await expect(detail.getByText('Northwind Traders Ltd.')).toBeVisible();
  await detail.getByRole('tab', { name: 'Key Terms' }).click();
  await expect(detail.getByTestId('key-term').first()).toContainText('Payment terms');
  await detail.getByRole('tab', { name: 'Risks' }).click();
  await expect(detail.getByText('Uncapped liability for data breaches')).toBeVisible();

  // Download opens the stored original (S3 presigned URL, stubbed).
  const popup = page.waitForEvent('popup');
  await detail.getByTestId('download-original').click();
  const original = await popup;
  await expect(original.locator('body')).toContainText('MASTER SERVICES AGREEMENT');
});

test('an upload over the size limit is refused with a clear message', async ({ page }) => {
  await signInAs(page, USERS.user);
  await page.goto('/contracts');
  await page.getByTestId('upload-contract-btn').click();
  await page.getByTestId('upload-title').fill('Too big');
  await page.getByTestId('upload-file').setInputFiles({
    name: 'big.txt',
    mimeType: 'text/plain',
    buffer: Buffer.alloc(11 * 1024 * 1024, 'a'),
  });
  await page.getByTestId('upload-submit').click();
  await expect(page.getByTestId('upload-failed')).toBeVisible();
  await expect(page.getByTestId('upload-failed-detail')).toContainText('maximum upload size is 10 MB');
});
