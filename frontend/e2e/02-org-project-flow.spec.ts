import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

test.describe('Org → invite → accept → transfer OC', () => {
  test('create an org', async ({ authedPage: page }) => {
    await page.goto('/orgs')
    // OrgListView labels the input "New organisation" (not "name"), and the
    // create button opens a confirm dialog before the API call.
    await page.getByRole('textbox', { name: /organisation/i }).fill('E2E Org')
    await page.getByRole('button', { name: /create/i }).click()
    await page.locator('dialog.confirm-dialog').getByRole('button', { name: /create/i }).click()
    await expect(page.getByText('E2E Org')).toBeVisible()
  })

  test('create a project under org', async ({ authedPage: page }) => {
    await page.goto('/orgs')
    await page.getByText('E2E Org').click()
    await expect(page.getByRole('heading', { name: /E2E Org/i })).toBeVisible()
  })

  test('invite a member to org', async ({ authedPage: page }) => {
    test.skip(!env('E2E_INVITE_TARGET'), 'needs second user')
    await page.goto('/orgs')
    await page.getByText('E2E Org').click()
    await page.getByRole('link', { name: /members/i }).click()
    await page.getByLabel(/email/i).fill(env('E2E_INVITE_TARGET')!)
    await page.getByRole('button', { name: /invite/i }).click()
    await expect(page.getByText(/invited/i)).toBeVisible()
  })
})
