import { test, expect } from './fixtures/auth'

test.describe('Notifications: bell badge → list → mark-read (M.2)', () => {
  test('notification bell is visible in the layout', async ({ authedPage: page }) => {
    await page.goto('/')
    await expect(page.locator('.notif-bell')).toBeVisible()
  })

  test('navigate to notifications list', async ({ authedPage: page }) => {
    await page.goto('/notifications')
    await expect(page).toHaveURL(/notifications/)
  })

  test('mark-all button exists on notifications page', async ({ authedPage: page }) => {
    await page.goto('/notifications')
    await expect(
      page.getByRole('button', { name: /mark.*all/i }).or(page.getByText(/no.*notification/i)),
    ).toBeVisible()
  })

  test('bell badge updates after mark-read', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_HAS_NOTIFICATIONS, 'needs seeded notifications')
    await page.goto('/notifications')
    const markAll = page.getByRole('button', { name: /mark.*all/i })
    test.skip(!(await markAll.isEnabled()), 'no unread notifications')
    await markAll.click()
    await page.goto('/')
    const badge = page.locator('.notif-bell__badge')
    if (await badge.isVisible()) {
      await expect(badge).not.toHaveText(/[1-9]/)
    }
  })
})
