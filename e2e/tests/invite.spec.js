const { test, expect } = require('@playwright/test');
const { PASSWORD, USERS, resetData, signInAs } = require('../fixtures/helpers');

test.beforeEach(async ({ request }) => {
  await resetData(request);
});

test('an admin invites a teammate who accepts and joins the team', async ({ page, browser, request }) => {
  const invitee = 'new.colleague@acme.example.com';
  await signInAs(page, USERS.admin);
  await page.goto('/team');
  await page.getByTestId('invite-member-btn').click();
  await page.getByTestId('invite-email').fill(invitee);
  await page.getByTestId('invite-role').click();
  await page.getByRole('option', { name: /Manager/ }).click();
  await page.getByTestId('invite-submit').click();
  await expect(page.getByText(`Invitation sent to ${invitee}`)).toBeVisible();

  // The invitation email is captured by the stub instead of going to Resend.
  const outbox = await (await request.get('/__e2e/outbox')).json();
  const mail = outbox.find((m) => m.to === invitee);
  expect(mail).toBeTruthy();
  expect(mail.role).toBe('manager');

  const inviteeContext = await browser.newContext();
  const inviteePage = await inviteeContext.newPage();
  await inviteePage.goto(mail.acceptUrl);
  await expect(inviteePage.getByTestId('accept-invite-email')).toHaveValue(invitee);
  await inviteePage.locator('#invite-first-name').fill('Nia');
  await inviteePage.locator('#invite-last-name').fill('Colleague');
  await inviteePage.getByTestId('accept-invite-password').fill(PASSWORD);
  await inviteePage.getByTestId('accept-invite-confirm-password').fill(PASSWORD);
  await inviteePage.getByTestId('accept-invite-submit').click();
  await expect(inviteePage.getByTestId('dashboard-page')).toBeVisible();

  // The link is single-use.
  const reuse = await inviteeContext.newPage();
  await reuse.goto(mail.acceptUrl);
  // Wait for the preview to settle on its terminal state before asserting
  // the form is absent (the form only renders after the preview loads).
  await expect(reuse.getByTestId('accept-invite-used')).toBeVisible();
  await expect(reuse.getByTestId('accept-invite-email')).toHaveCount(0);
  await inviteeContext.close();

  // The admin now sees the new member.
  await page.reload();
  await expect(page.getByText(invitee).first()).toBeVisible();
});

test('only admins can invite', async ({ page, request }) => {
  await signInAs(page, USERS.manager);
  await page.goto('/team');
  await expect(page.getByTestId('invite-member-btn')).toHaveCount(0);
  const { access_token: token } = await (
    await request.post('/__e2e/session', { data: { email: USERS.manager } })
  ).json();
  const res = await request.post('/api/v1/team/invite', {
    headers: { Authorization: `Bearer ${token}` },
    data: { email: 'x@acme.example.com', role: 'user' },
  });
  expect(res.status()).toBe(403);
});
