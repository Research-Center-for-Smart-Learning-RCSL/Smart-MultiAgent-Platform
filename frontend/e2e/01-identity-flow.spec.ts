import { test, expect } from '@playwright/test'
import { seedUser } from './fixtures/auth'

test.describe('Identity flow: Register → verify → login', () => {
  test('register a new account', async ({ page }) => {
    // Non-reserved domain — the backend's EmailStr 422s reserved TLDs like .test.
    const email = `e2e-${Date.now()}@example.com`
    const password = 'E2eP@ssw0rd!Str0ng'

    await page.goto('/register')
    await page.getByLabel(/email/i).fill(email)
    await page.getByLabel(/password/i).fill(password)
    // The test stack's captcha config is provider=off, so RegisterView renders
    // no captcha widget and the backend skips verification — register submits
    // with email + password only.
    await page.getByRole('button', { name: /register|sign up|submit/i }).click()
    // RegisterView.vue redirects to /login?pendingVerify=1 on success.
    await page.waitForURL(/\/login\?.*pendingVerify=1/)
    await expect(page).toHaveURL(/pendingVerify=1/)
  })

  test('verify email via token', async ({ page }) => {
    // The seed routine pre-verifies the fixture user; this test only runs
    // when an out-of-band token is supplied (e.g. extracted from a test mailer).
    test.skip(!process.env.E2E_VERIFY_TOKEN, 'needs seeded verify token')
    // Token rides in the URL fragment, not the query string (SEC-8).
    await page.goto(`/verify-email#token=${process.env.E2E_VERIFY_TOKEN}`)
    await expect(page.getByText(/verified|success/i)).toBeVisible()
  })

  test('login with seeded verified account', async ({ page }) => {
    // Gate on the boot refresh — same pattern as auth.ts. On Vite cold start the
    // app mounts only after hydrate() resolves; clicking before that means no
    // /api/auth/login fires and we silently stay on /login.
    const bootRefresh = page
      .waitForResponse((r) => r.url().includes('/api/auth/refresh'), { timeout: 30_000 })
      .catch(() => undefined)
    await page.goto('/login')
    await bootRefresh
    await page.getByLabel(/email/i).fill(seedUser.email)
    await page.getByLabel(/password/i).fill(seedUser.password)
    await page.getByRole('button', { name: /log\s*in|sign\s*in|submit/i }).click()
    // /(?!.*login).*/ is vacuously true (matches empty string at end of any URL)
    // and resolves before the redirect fires. Use a predicate instead.
    await page.waitForURL((url) => !url.pathname.includes('/login'))
    await expect(page).not.toHaveURL(/login/)
  })
})
