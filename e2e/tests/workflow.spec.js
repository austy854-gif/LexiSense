const { test, expect } = require('@playwright/test');
const { USERS, resetData, signInAs, apiUpload } = require('../fixtures/helpers');

test.beforeEach(async ({ request }) => {
  await resetData(request);
});

test('a user submits a contract for review and a manager approves it', async ({ browser, request }) => {
  const contract = await apiUpload(request, USERS.user, 'Review me MSA');

  // The uploader submits it for review.
  const userContext = await browser.newContext();
  const userPage = await userContext.newPage();
  await signInAs(userPage, USERS.user);
  await userPage.goto(`/contracts/${contract.id}`);
  const actions = userPage.getByTestId('workflow-actions');
  await expect(actions.getByTestId('workflow-approve')).toHaveCount(0);
  await actions.getByTestId('workflow-submit').click();
  await expect(userPage.getByTestId('contract-detail-page').getByText('In Review').first()).toBeVisible();
  await expect(actions.getByTestId('workflow-submit')).toHaveCount(0);
  // A user cannot approve their own submission.
  await expect(actions.getByTestId('workflow-approve')).toHaveCount(0);
  await userContext.close();

  // A manager approves, then activates.
  const managerContext = await browser.newContext();
  const managerPage = await managerContext.newPage();
  await signInAs(managerPage, USERS.manager);
  await managerPage.goto(`/contracts/${contract.id}`);
  const managerActions = managerPage.getByTestId('workflow-actions');
  await managerActions.getByTestId('workflow-approve').click();
  await expect(managerActions.getByTestId('workflow-activate')).toBeVisible();
  await managerActions.getByTestId('workflow-activate').click();
  await expect(managerActions.getByTestId('workflow-activate')).toHaveCount(0);
  await managerContext.close();

  const { access_token: token } = await (
    await request.post('/__e2e/session', { data: { email: USERS.manager } })
  ).json();
  const res = await request.get(`/api/v1/contracts/${contract.id}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect((await res.json()).status).toBe('active');
});

test('a manager can send a contract back by rejecting it', async ({ page, request }) => {
  const contract = await apiUpload(request, USERS.user, 'Reject me MSA');
  const { access_token: token } = await (
    await request.post('/__e2e/session', { data: { email: USERS.user } })
  ).json();
  const submitted = await request.post(
    `/api/v1/contracts/${contract.id}/workflow/submit_for_review`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  expect(submitted.status()).toBe(200);

  await signInAs(page, USERS.manager);
  await page.goto(`/contracts/${contract.id}`);
  await page.getByTestId('workflow-reject').click();
  await expect(page.getByTestId('workflow-approve')).toHaveCount(0);
  await expect(page.getByTestId('contract-detail-page').getByText('Draft').first()).toBeVisible();
});
