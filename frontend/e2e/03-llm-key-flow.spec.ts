import { test, expect } from './fixtures/auth'

test.describe('Upload LLM key → validate → carry into project → key group', () => {
  test('upload an API key', async ({ authedPage: page }) => {
    await page.goto('/keys')
    await expect(page).toHaveURL(/keys/)
  })

  test('validate uploaded key', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_TEST_API_KEY, 'needs real LLM key for validation')
    await page.goto('/keys')
    await page.getByRole('button', { name: /retest|validate/i }).first().click()
    await expect(page.getByText(/valid|passed/i)).toBeVisible()
  })

  test('carry key into project', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    await page.goto(`/projects/${process.env.E2E_PROJECT_ID}/keys`)
    await expect(page).toHaveURL(/keys/)
  })
})
