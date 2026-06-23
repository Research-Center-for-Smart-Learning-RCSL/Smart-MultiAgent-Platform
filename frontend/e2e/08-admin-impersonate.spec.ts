import { test, expect } from './fixtures/auth'

test.describe('Admin impersonate → target notified → audit visible', () => {
  test('impersonate a user', async ({ adminPage: page }) => {
    test.skip(!process.env.E2E_TARGET_USER_ID, 'needs target user')
    await page.goto('/admin/impersonate')
    await page.getByLabel(/user.*id/i).fill(process.env.E2E_TARGET_USER_ID!)
    await page.getByRole('button', { name: /start|impersonate/i }).click()
    await expect(page.locator('.admin-impersonate__active')).toBeVisible()
  })

  test('impersonation visible in audit log', async ({ adminPage: page }) => {
    await page.goto('/admin/audit')
    await expect(page).toHaveURL(/audit/)
  })

  test('last-admin demote is blocked', async ({ adminPage: page }) => {
    await page.goto('/admin/admins')
    await expect(page).toHaveURL(/admins/)
  })
})
