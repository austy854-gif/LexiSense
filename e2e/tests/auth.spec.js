const { test, expect } = require('@playwright/test');
const { PASSWORD, USERS, resetData } = require('../fixtures/helpers');

test.beforeEach(async ({ request }) => {
  await resetData(request);
});

test('a new customer signs up and lands on their dashboard', async ({ page }) => {
  await page.goto('/register');
  await page.getByTestId('register-firstname').fill('Nora');
  await page.getByTestId('register-lastname').fill('New');
  await page.getByTestId('register-email').fill('nora@newco.example.com');
  await page.getByTestId('register-org').fill('NewCo');
  await page.getByTestId('register-password').fill(PASSWORD);
  await page.getByTestId('register-confirm-password').fill(PASSWORD);
  await page.getByTestId('register-submit').click();

  await expect(page.getByTestId('dashboard-page')).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
  // The owner of a new organization is its admin and can manage the team.
  await expect(page.getByTestId('nav-team')).toBeVisible();
});

test('an existing user logs in, and a wrong password is refused', async ({ page }) => {
  await page.goto('/login');
  await page.getByTestId('login-email').fill(USERS.manager);
  await page.getByTestId('login-password').fill('not-the-password');
  await page.getByTestId('login-submit').click();
  await expect(page.getByText('Invalid email or password').first()).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);

  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-submit').click();
  await expect(page.getByTestId('dashboard-page')).toBeVisible();

  await page.getByTestId('user-menu').click();
  await page.getByTestId('logout-btn').click();
  await expect(page).toHaveURL(/\/login|\/$/);
  await page.goto('/contracts');
  await expect(page).toHaveURL(/\/login/);
});
