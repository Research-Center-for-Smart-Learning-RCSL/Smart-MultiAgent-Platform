import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

test.describe('Workflow editor → validate → run → live steps', () => {
  test('create a workflow', async ({ authedPage: page }) => {
    test.skip(!env('E2E_WORKSPACE_ID'), 'needs seeded workspace')
    const wsId = env('E2E_WORKSPACE_ID')!
    await page.goto(`/workspaces/${wsId}/workflows`)
    await expect(page).toHaveURL(/workflows/)
  })

  test('validate workflow shows inline errors', async ({ authedPage: page }) => {
    test.skip(!env('E2E_WORKSPACE_ID'), 'needs seeded workspace')
    test.skip(!env('E2E_WORKFLOW_ID'), 'needs seeded workflow')
    await page.goto(`/workspaces/${env('E2E_WORKSPACE_ID')}/workflows/${env('E2E_WORKFLOW_ID')}/edit`)
    await page.getByRole('button', { name: /validate/i }).click()
    await expect(page.locator('[data-testid="lint-status"]')).toBeVisible({ timeout: 5000 })
  })

  test('trigger run and observe live steps', async ({ authedPage: page }) => {
    test.skip(!env('E2E_WORKSPACE_ID'), 'needs seeded workspace')
    test.skip(!env('E2E_WORKFLOW_ID'), 'needs seeded workflow')
    await page.goto(`/workspaces/${env('E2E_WORKSPACE_ID')}/workflows/${env('E2E_WORKFLOW_ID')}/runs`)
    await page.getByRole('button', { name: /trigger|run/i }).click()
    await expect(page.getByText(/running|completed|waiting/i)).toBeVisible({ timeout: 30_000 })
  })
})
