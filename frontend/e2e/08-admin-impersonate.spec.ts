import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// Copy resolved from src/slices/admin/locales/en.json:
//   impersonation.targetPlaceholder -> "Target user UUID"
//   impersonation.start             -> "Start Session"
//   impersonation.confirmStart      -> "Start Session"  (confirm dialog)
//   audit.title                     -> "Audit Log"
//   admins.title                    -> "Admins"

test.describe('Admin impersonate → target notified → audit visible', () => {
  test('impersonate a user', async ({ adminPage: page }) => {
    test.skip(!env('E2E_TARGET_USER_ID'), 'needs target user')
    await page.goto('/admin/impersonate')

    const form = page.locator('form.admin-impersonate__form')
    // Located by placeholder, not label: the view passes :aria-label to SInput,
    // whose root is a wrapper <div>, so the attribute lands on the div and the
    // <input> ends up with no accessible name at all.
    await form.getByPlaceholder('Target user UUID').fill(env('E2E_TARGET_USER_ID')!)
    await form.getByRole('button', { name: 'Start Session' }).click()

    // onStart gates the mutation behind SConfirmDialog (role="alertdialog"),
    // whose confirm button reuses the same "Start Session" label — scope to the
    // dialog so the locator is not ambiguous.
    const dialog = page.getByRole('alertdialog')
    await expect(dialog).toBeVisible({ timeout: 10_000 })
    await dialog.getByRole('button', { name: 'Start Session' }).click()

    await expect(page.locator('.admin-impersonate__active')).toBeVisible({ timeout: 10_000 })
  })

  test('impersonation visible in audit log', async ({ adminPage: page }) => {
    await page.goto('/admin/audit')
    await expect(page).toHaveURL(/audit/)
    await expect(page.getByRole('heading', { name: 'Audit Log', level: 1 })).toBeVisible({
      timeout: 15_000,
    })
  })

  test('last-admin demote is blocked', async ({ adminPage: page }) => {
    await page.goto('/admin/admins')
    await expect(page).toHaveURL(/admins/)
    await expect(page.getByRole('heading', { name: 'Admins', level: 1 })).toBeVisible({
      timeout: 15_000,
    })
  })
})
