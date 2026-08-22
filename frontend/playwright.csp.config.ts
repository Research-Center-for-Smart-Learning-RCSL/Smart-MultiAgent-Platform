import { defineConfig, devices } from '@playwright/test'

/**
 * The CSP-fronted font check (`e2e-csp/`), separate from the main e2e config
 * for two reasons that are both load-bearing.
 *
 * It needs a **built** frontend. Vite's dev server injects inline module
 * preambles that `script-src 'self' 'wasm-unsafe-eval'` blocks outright, so
 * running this against `pnpm dev` would fail on the SPA never booting and say
 * nothing about the font.
 *
 * And it must not be picked up by `playwright.config.ts`, whose `testDir` is
 * `./e2e` and whose baseURL is Vite. Hence its own directory rather than a
 * `26-` spec beside the others.
 *
 * `PLAYWRIGHT_CSP_BASE_URL` is set by the `frontend-csp-font` job, which is
 * where the port is actually chosen; the fallback below only serves a local
 * run that publishes the container on the same port by hand. There is
 * deliberately no `webServer`: the server under test is nginx carrying a
 * specific header, which is the entire subject of the check, so starting one
 * here would test a server this repository invented instead.
 */
export default defineConfig({
  testDir: './e2e-csp',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  timeout: 60_000,

  use: {
    baseURL: process.env.PLAYWRIGHT_CSP_BASE_URL ?? 'http://localhost:8080',
    trace: 'retain-on-failure',
  },

  projects: [{ name: 'desktop', use: { ...devices['Desktop Chrome'] } }],
})
