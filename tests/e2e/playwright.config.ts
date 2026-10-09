import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: '*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.SPLASH_E2E_URL || 'http://127.0.0.1:5173',
    headless: true,
    launchOptions: process.env.SPLASH_CHROMIUM_PATH
      ? { executablePath: process.env.SPLASH_CHROMIUM_PATH }
      : {},
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  reporter: [['list'], ['json', { outputFile: 'test-results/results.json' }]],
});
