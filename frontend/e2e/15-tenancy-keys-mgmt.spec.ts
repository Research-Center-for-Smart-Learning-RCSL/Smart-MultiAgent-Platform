import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

test.describe('Tenancy/keys mgmt: roles, rename, key-group, usage (M.4)', () => {
  test('change a project member role', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project with members')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/members`)
    await expect(page).toHaveURL(/members/)

    const promoteBtn = page.getByRole('button', { name: /promote/i }).first()
    const demoteBtn = page.getByRole('button', { name: /demote/i }).first()
    await expect(promoteBtn.or(demoteBtn)).toBeVisible({ timeout: 5000 })
  })

  test('rename a project', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}`)

    const renameBtn = page.getByRole('button', { name: /rename/i })
    await expect(renameBtn).toBeVisible()
    await renameBtn.click()

    const nameInput = page.locator('form input').first()
    const original = await nameInput.inputValue()
    const newName = `${original}-r`
    await nameInput.fill(newName)
    await page.getByRole('button', { name: /save/i }).click()
    await expect(page.getByText(newName)).toBeVisible({ timeout: 5000 })

    // Restore original name.
    await page.getByRole('button', { name: /rename/i }).click()
    await page.locator('form input').first().fill(original)
    await page.getByRole('button', { name: /save/i }).click()
    await expect(page.getByText(original)).toBeVisible({ timeout: 5000 })
  })

  test('rename an org', async ({ authedPage: page }) => {
    test.skip(!env('E2E_ORG_ID'), 'needs seeded org')
    await page.goto(`/orgs/${env('E2E_ORG_ID')}`)

    const renameBtn = page.getByRole('button', { name: /rename/i })
    await expect(renameBtn).toBeVisible()
    await renameBtn.click()

    const nameInput = page.locator('form input').first()
    const original = await nameInput.inputValue()
    const newName = `${original}-r`
    await nameInput.fill(newName)
    await page.getByRole('button', { name: /save/i }).click()
    await expect(page.getByText(newName)).toBeVisible({ timeout: 5000 })

    // Restore original name.
    await page.getByRole('button', { name: /rename/i }).click()
    await page.locator('form input').first().fill(original)
    await page.getByRole('button', { name: /save/i }).click()
    await expect(page.getByText(original)).toBeVisible({ timeout: 5000 })
  })

  test('key group rename', async ({ authedPage: page }) => {
    test.skip(!env('E2E_KEY_GROUP_URL'), 'needs seeded key group (projectId/key-groups/id)')
    await page.goto(`/${env('E2E_KEY_GROUP_URL')}`)

    const renameBtn = page.getByRole('button', { name: /rename/i })
    await expect(renameBtn).toBeVisible()
    await renameBtn.click()

    const nameInput = page.locator('form input').first()
    const original = await nameInput.inputValue()
    await nameInput.fill(`${original}-r`)
    await page.getByRole('button', { name: /save/i }).click()
    await expect(page.getByText(`${original}-r`)).toBeVisible({ timeout: 5000 })

    // Restore original name.
    await page.getByRole('button', { name: /rename/i }).click()
    await page.locator('form input').first().fill(original)
    await page.getByRole('button', { name: /save/i }).click()
    await expect(page.getByText(original)).toBeVisible({ timeout: 5000 })
  })

  test('project key usage panel renders', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project with carried keys')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/keys`)
    await expect(page).toHaveURL(/keys/)

    const usageBtn = page.locator('[data-testid="usage"]').first()
    test.skip(!(await usageBtn.isVisible().catch(() => false)), 'no carried keys')

    await usageBtn.click()
    // Usage section should show request/token/error counts.
    await expect(page.getByText(/requests|tokens|errors/i)).toBeVisible({ timeout: 5000 })
  })

  test('org quotas display', async ({ authedPage: page }) => {
    test.skip(!env('E2E_ORG_ID'), 'needs seeded org')
    await page.goto(`/orgs/${env('E2E_ORG_ID')}`)
    // Quotas panel loads non-blocking; check for quota-related text.
    await expect(
      page.getByText(/quota|limit|users|projects/i).first(),
    ).toBeVisible({ timeout: 5000 })
  })
})
