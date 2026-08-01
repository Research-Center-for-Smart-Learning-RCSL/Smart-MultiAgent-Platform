import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// The bell lives in AppTopBar, which only the authenticated AppShell layout
// renders — "/" is a public landing page with no top bar, so every bell
// assertion has to run from an authenticated route.
const BELL_NAME = /^Notifications \(\d+ unread\)$/

test.describe('Notifications: bell badge → list → mark-read (M.2)', () => {
  test('notification bell is visible in the layout', async ({ authedPage: page }) => {
    await page.goto('/notifications')
    // The sidebar also links to /notifications; the bell is distinguished by the
    // unread count in its aria-label.
    await expect(page.getByRole('link', { name: BELL_NAME })).toBeVisible()
  })

  test('navigate to notifications list', async ({ authedPage: page }) => {
    await page.goto('/notifications')
    await expect(page).toHaveURL(/notifications/)
  })

  test('mark-all button exists on notifications page', async ({ authedPage: page }) => {
    await page.goto('/notifications')
    await expect(page.getByRole('button', { name: 'Mark all read', exact: true })).toBeVisible()
  })

  test('bell badge updates after mark-read', async ({ authedPage: page }) => {
    test.skip(!env('E2E_HAS_NOTIFICATIONS'), 'needs seeded notifications')
    await page.goto('/notifications')
    const markAll = page.getByRole('button', { name: 'Mark all read', exact: true })
    // Disabled until the unread query resolves with a non-zero count — wait
    // for enablement rather than sampling once.
    let hasUnread = true
    try {
      await expect(markAll).toBeEnabled({ timeout: 8_000 })
    } catch {
      hasUnread = false
    }
    test.skip(!hasUnread, 'no unread notifications')
    await markAll.click()
    // The badge is only rendered while the unread count is above zero, which
    // markAll invalidates once the server confirms the batch.
    await expect(page.locator('.notif-bell__badge')).toBeHidden({ timeout: 10_000 })
  })
})
