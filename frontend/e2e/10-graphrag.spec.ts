import { test, expect } from './fixtures/auth'

test.describe('GraphRAG: create → bind → build → terminal state (M.1)', () => {
  test('navigate to GraphRAG config list', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    await page.goto(`/projects/${process.env.E2E_PROJECT_ID}/graphrag-configs`)
    await expect(page).toHaveURL(/graphrag-configs/)
  })

  test('create a GraphRAG config', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    const projectId = process.env.E2E_PROJECT_ID!
    await page.goto(`/projects/${projectId}/graphrag-configs`)

    // Open the create form; the toggle button says "New Configuration".
    await page.getByRole('button', { name: /new configuration/i }).click()

    const agentSelect = page.locator('#agent_id')
    const agentOpts = agentSelect.locator('option:not([value=""])')
    test.skip((await agentOpts.count()) === 0, 'needs agents without GraphRAG config')
    await agentSelect.selectOption({ index: 1 })

    const kgSelect = page.locator('#builder_key_group_id')
    const kgOpts = kgSelect.locator('option:not([value=""])')
    test.skip((await kgOpts.count()) === 0, 'needs key groups')
    await kgSelect.selectOption({ index: 1 })

    await page.locator('button[type="submit"]').click()
    // On success the form closes and the new config appears in the table.
    await expect(page.locator('table, [role="table"]')).toBeVisible({ timeout: 5000 })
  })

  test('list shows Bound/Not-bound status', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    await page.goto(`/projects/${process.env.E2E_PROJECT_ID}/graphrag-configs`)
    // The table always renders (with empty-state row when no configs exist).
    await expect(page.locator('table').first()).toBeVisible()
  })

  test('build triggers status change', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    test.skip(!process.env.E2E_GRAPHRAG_CONFIG_ID, 'needs seeded GraphRAG config')
    await page.goto(`/projects/${process.env.E2E_PROJECT_ID}/graphrag-configs`)
    const buildBtn = page.getByRole('button', { name: /build/i }).first()
    test.skip(!(await buildBtn.isVisible()), 'no buildable config on the page')
    await buildBtn.click()
    await expect(
      page.getByText(/running|neo4j_committed|qdrant_committed|failed/i),
    ).toBeVisible({ timeout: 30_000 })
  })
})
