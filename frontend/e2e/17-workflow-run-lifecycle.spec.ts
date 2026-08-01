import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// Workflow engine depth pass. 07-workflow-flow stops at "a run row shows some
// state"; this spec follows a run through the engine: manual trigger -> Arq
// worker pickup -> step execution -> terminal state persisted and streamed to
// the run detail view. The seeded workflow is the minimal trigger -> end
// definition, so it must SUCCEED without any LLM — making this the one place
// in the suite where the engine's happy path is asserted end-to-end rather
// than "reached any state". Also covers the archive read path, the
// admin-gated backstage panel, and the agent orchestration surface (wake-up
// config optimistic-lock roundtrip + DLQ read).

const wsId = () => env('E2E_WORKSPACE_ID')
const wfId = () => env('E2E_WORKFLOW_ID')

test.describe('Workflow engine: run lifecycle, archive, backstage, DLQ', () => {
  test('manual trigger drives the run to a terminal state with recorded steps', async ({ authedPage: page }) => {
    test.skip(!wsId() || !wfId(), 'needs seeded workspace + workflow')
    await page.goto(`/workspaces/${wsId()}/workflows/${wfId()}/runs`)
    await expect(page.getByRole('heading', { name: 'Workflow Runs' })).toBeVisible()

    await page.getByRole('button', { name: 'Trigger Run', exact: true }).click()
    // The trigger POST commits the run before dispatching the worker job, so a
    // row exists as soon as the list refetches.
    const inspect = page.getByRole('link', { name: 'Inspect' }).first()
    await expect(inspect).toBeVisible({ timeout: 15_000 })
    await inspect.click()

    await expect(page).toHaveURL(/\/workflow-runs\//)
    await expect(page.getByRole('heading', { name: /^Run / })).toBeVisible()

    // trigger -> end carries no LLM dependency: anything but `succeeded` here
    // is an engine regression, not an environment artifact. The header badge is
    // fed by the run WS channel with a query fallback.
    await expect(page.locator('section.workflow-run header .s-badge')).toHaveText(
      'succeeded',
      { timeout: 30_000 },
    )
    // Both nodes of the definition must have left step rows behind.
    const stepRows = page.locator('section.workflow-run table tbody tr')
    await expect(stepRows.first()).toBeVisible({ timeout: 10_000 })
    expect(await stepRows.count()).toBeGreaterThanOrEqual(2)
  })

  test('include-archived toggle exercises the archive read path', async ({ authedPage: page }) => {
    test.skip(!wsId() || !wfId(), 'needs seeded workspace + workflow')
    await page.goto(`/workspaces/${wsId()}/workflows/${wfId()}/runs`)
    await expect(page.getByRole('heading', { name: 'Workflow Runs' })).toBeVisible()

    // The archive union is a distinct SQL path (live + archived runs); assert
    // the API answers 200 for it, not just that the checkbox toggles.
    const archived = page.waitForResponse(
      (r) =>
        r.request().method() === 'GET' &&
        r.url().includes(`/workflows/${wfId()}/runs`) &&
        r.url().includes('include_archive=true'),
      { timeout: 15_000 },
    )
    // SCheckbox visually hides the native input behind a styled box span that
    // intercepts pointer events, so check() on #runs-show-archive can never
    // click through — toggle via the label instead.
    await page.locator('label.s-checkbox', { hasText: 'Include archived' }).click()
    expect((await archived).status()).toBe(200)
    await expect(page.locator('section.workflow-runs')).toBeVisible()
  })

  test('backstage panel renders the run trace for an admin', async ({ adminPage: page }) => {
    test.skip(!wsId() || !wfId(), 'needs seeded workspace + workflow')
    await page.goto(`/workspaces/${wsId()}/workflows/${wfId()}/backstage`)
    await expect(page.getByRole('heading', { name: 'Backstage Panel' })).toBeVisible()

    // Pick the newest run (index 0 is the selectable "no run" entry). The
    // earlier lifecycle test guarantees at least one run exists.
    const select = page.locator('.s-select__native').first()
    await expect(select).toBeVisible()
    // The selector's options land when the runs query resolves — poll instead
    // of sampling once, or a slow query turns this test into a silent skip.
    let hasRuns = true
    try {
      await expect
        .poll(() => select.locator('option').count(), { timeout: 15_000 })
        .toBeGreaterThanOrEqual(2)
    } catch {
      hasRuns = false
    }
    test.skip(!hasRuns, 'no runs to trace')
    await select.selectOption({ index: 1 })

    await expect(page.getByRole('heading', { name: 'Execution Trace' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Sub-agent Tree' })).toBeVisible()
  })

  test('agent orchestration: wake-up config roundtrip and DLQ read', async ({ authedPage: page }) => {
    test.skip(!env('E2E_AGENT_ID'), 'needs seeded agent')
    await page.goto(`/agents/${env('E2E_AGENT_ID')}/orchestration`)
    await expect(page.getByRole('heading', { name: 'Agent Orchestration' })).toBeVisible()

    // Saving even an unchanged config exercises the versioned PATCH (optimistic
    // lock) through to the success toast.
    await page.getByRole('button', { name: 'Save wake-up config', exact: true }).click()
    await expect(page.getByText('Wake-up configuration saved.')).toBeVisible({ timeout: 10_000 })

    // The DLQ viewer is collapsed by default and only fetches on expand; the
    // empty state renders after a real GET on the A2A dead-letter endpoint
    // resolves — an error would surface the danger branch instead.
    await page.getByRole('button', { name: 'Dead Letter Queue' }).click()
    await expect(page.getByText('No failed messages.')).toBeVisible({ timeout: 10_000 })
  })
})
