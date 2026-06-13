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
    await page.goto('/login')
    await page.getByLabel(/email/i).fill(seedUser.email)
    await page.getByLabel(/password/i).fill(seedUser.password)
    await page.getByRole('button', { name: /log\s*in|sign\s*in|submit/i }).click()
    await page.waitForURL(/(?!.*login).*/)
    await expect(page).not.toHaveURL(/login/)
  })
})
