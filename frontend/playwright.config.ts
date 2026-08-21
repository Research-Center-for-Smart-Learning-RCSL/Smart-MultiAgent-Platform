import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  // `github` alone writes annotations but no report directory, so a CI failure
  // left nothing behind to inspect — the screenshot/trace paths it prints live
  // under `test-results/`, and the workflow's artifact upload of
  // `playwright-report/` came back empty. Emit the HTML report too.
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'html',
  timeout: 60_000,

  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  // 11-responsive-a11y.md §10 asks for more than one viewport. The three narrow
  // projects are testMatch-scoped to the one spec whose assertions are
  // viewport-conditional, deliberately: this config is `workers: 1` and CI runs
  // a bare `pnpm run test:e2e`, so letting every project run every spec would
  // turn 22 serial specs into 88 to answer three questions. The cost of the
  // scoping is that the golden paths are still exercised at desktop width only
  // (dossier FU-8).
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // Exactly at the md breakpoint, which is the boundary F-39 got wrong.
      name: 'tablet',
      testMatch: /23-mobile-viewport\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 768, height: 1024 } },
    },
    {
      name: 'mobile',
      testMatch: /23-mobile-viewport\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 375, height: 812 } },
    },
    {
      // Below the 362px threshold at which the sidebar stops fitting its
      // drawer, which is what makes F-42's overflow observable at all.
      name: 'mobile-xs',
      testMatch: /23-mobile-viewport\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 320, height: 568 } },
    },
  ],

  webServer: {
    command: 'pnpm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
