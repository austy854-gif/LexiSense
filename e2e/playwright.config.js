// @ts-check
const { defineConfig, devices } = require('@playwright/test');

const BASE_URL = process.env.E2E_BASE_URL || 'http://localhost:8000';

/**
 * End-to-end suite for the launch-critical flows. It drives the real built
 * SPA served by the real API (backend/e2e/stub_app.py) against a real Mongo,
 * with AI, storage, email and Stripe stubbed. See docs/e2e.md.
 */
module.exports = defineConfig({
  testDir: './tests',
  // Every test resets the shared database, so tests run one at a time.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 7_500 },
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }], ['github']]
    : [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  outputDir: 'test-results',
  use: {
    baseURL: BASE_URL,
    // Saved for every failed test (and its retry) and uploaded by CI.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    actionTimeout: 7_500,
    navigationTimeout: 15_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.E2E_SKIP_SERVER
    ? undefined
    : {
        command: 'bash ./scripts/start-backend.sh',
        url: `${BASE_URL}/api/v1/ready`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        stdout: 'pipe',
        stderr: 'pipe',
      },
});
