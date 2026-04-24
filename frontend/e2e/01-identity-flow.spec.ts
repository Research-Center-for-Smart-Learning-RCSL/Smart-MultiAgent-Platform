import { test, expect } from '@playwright/test'

test.describe('Identity flow: Register → verify → login', () => {
  const email = `e2e-${Date.now()}@smap.test`
  const password = 'E2eP@ssw0rd!Str0ng'

  test('register a new account', async ({ page }) => {
    await page.goto('/register')
    await page.getByLabel(/email/i).fill(email)
    await page.getByLabel(/password/i).fill(password)
    await page.getByRole('button', { name: /register|sign up|submit/i }).click()
    await expect(page.getByText(/verify|check your email/i)).toBeVisible()
  })

  test('verify email via token', async ({ page }) => {
    // In compose.test.yml the mailer is stubbed — token extracted from test DB
    test.skip(!process.env.E2E_VERIFY_TOKEN, 'needs seeded verify token')
    await page.goto(`/verify-email?token=${process.env.E2E_VERIFY_TOKEN}`)
    await expect(page.getByText(/verified|success/i)).toBeVisible()
  })

  test('login with verified account', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel(/email/i).fill(email)
    await page.getByLabel(/password/i).fill(password)
    await page.getByRole('button', { name: /log\s*in|sign\s*in|submit/i }).click()
    await page.waitForURL(/(?!.*login).*/)
    await expect(page).not.toHaveURL(/login/)
  })
})
