import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

test.describe('Upload LLM key → validate → carry into project → key group', () => {
  test('upload an API key', async ({ authedPage: page }) => {
    await page.goto('/keys')
    await expect(page).toHaveURL(/keys/)

    // Open the upload form and fill fields.
    const providerSelect = page.locator('[data-testid="key-provider"]')
    await expect(providerSelect).toBeVisible({ timeout: 10_000 })
    await providerSelect.selectOption('openai')

    await page.locator('[data-testid="key-name"]').fill(`e2e-key-${Date.now()}`)
    await page.locator('[data-testid="key-secret"]').fill('sk-test-00000000000000000000000000000000')
    await page.locator('[data-testid="key-upload-submit"]').click()

    // The new key should appear in the table.
    await expect(page.locator('[data-testid="key-list"] tbody tr').first()).toBeVisible({
      timeout: 5000,
    })
  })

  test('retest uploaded key', async ({ authedPage: page }) => {
    await page.goto('/keys')
    // Wait for the key list table to finish async loading before checking buttons.
    const keyList = page.locator('[data-testid="key-list"]')
    await expect(keyList).toBeVisible({ timeout: 10_000 })

    const retestBtn = page.locator('[data-testid="retest"]').first()
    test.skip((await retestBtn.count()) === 0, 'no keys to retest')

    await retestBtn.click()
    // Status cell has a dynamic class like "status-valid" or "status-failed".
    await expect(
      keyList.locator('tbody tr').first().locator('td[class*="status-"]'),
    ).toBeVisible({ timeout: 5000 })
  })

  test('carry key into project via key groups', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    const projectId = env('E2E_PROJECT_ID')!

    // Navigate to project key groups.
    await page.goto(`/projects/${projectId}/key-groups`)
    await expect(page).toHaveURL(/key-groups/)

    const groupList = page.locator('[data-testid="group-list"]')
    await expect(groupList).toBeVisible({ timeout: 10_000 })

    // If no group exists, create one.
    const hasGroups = (await groupList.locator('a').count()) > 0
    if (!hasGroups) {
      await page.locator('[data-testid="group-name"]').fill(`e2e-group-${Date.now()}`)
      await page.locator('[data-testid="group-create"]').click()
      await expect(groupList.locator('a').first()).toBeVisible({ timeout: 5000 })
    }

    // Verify key group link is navigable.
    await groupList.locator('a').first().click()
    await expect(page).toHaveURL(/key-groups\//)
  })

  test('project keys page loads', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/keys`)
    await expect(page).toHaveURL(/keys/)
  })
})
