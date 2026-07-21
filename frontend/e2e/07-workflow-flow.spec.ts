import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// Copy resolved from src/slices/workflow/locales/en.json:
//   list.title        -> "Workflows"    (SPageHeader renders it as <h1>)
//   editor.validate   -> "Validate"
//   runs.triggerManual-> "Trigger Run"
// SStatusBadge renders the raw RunState string, so the state cell text is
// exactly one of running|waiting|succeeded|failed|cancelled.

test.describe('Workflow editor → validate → run → live steps', () => {
  test('create a workflow', async ({ authedPage: page }) => {
    test.skip(!env('E2E_WORKSPACE_ID'), 'needs seeded workspace')
    const wsId = env('E2E_WORKSPACE_ID')!
    await page.goto(`/workspaces/${wsId}/workflows`)
    await expect(page).toHaveURL(/workflows/)
    await expect(page.getByRole('heading', { name: 'Workflows', level: 1 })).toBeVisible({
      timeout: 15_000,
    })
  })

  test('validate workflow shows inline errors', async ({ authedPage: page }) => {
    test.skip(!env('E2E_WORKSPACE_ID'), 'needs seeded workspace')
    test.skip(!env('E2E_WORKFLOW_ID'), 'needs seeded workflow')
    await page.goto(`/workspaces/${env('E2E_WORKSPACE_ID')}/workflows/${env('E2E_WORKFLOW_ID')}/edit`)

    // The toolbar renders before the workflow loads, and onValidate serialises
    // whatever is on the canvas — clicking early would validate an empty graph.
    // The <h2> only appears once loadWorkflow() has resolved the workflow.
    await expect(page.locator('.workflow-editor h2')).toBeVisible({ timeout: 20_000 })

    await page.getByRole('button', { name: 'Validate' }).click()
    await expect(page.locator('[data-testid="lint-status"]')).toBeVisible({ timeout: 10_000 })
  })

  test('trigger run and observe live steps', async ({ authedPage: page }) => {
    test.skip(!env('E2E_WORKSPACE_ID'), 'needs seeded workspace')
    test.skip(!env('E2E_WORKFLOW_ID'), 'needs seeded workflow')
    await page.goto(`/workspaces/${env('E2E_WORKSPACE_ID')}/workflows/${env('E2E_WORKFLOW_ID')}/runs`)

    await page.getByRole('button', { name: 'Trigger Run' }).click()

    // RunState vocabulary is running|waiting|succeeded|failed|cancelled — a
    // trigger->end run reaches "succeeded" almost immediately (no "completed").
    // Scope to the runs table: the page chrome also contains the words "Run"
    // and "Runs".
    await expect(
      page
        .getByRole('table')
        .getByText(/^(running|waiting|succeeded|failed|cancelled)$/i)
        .first(),
    ).toBeVisible({ timeout: 30_000 })
  })
})
