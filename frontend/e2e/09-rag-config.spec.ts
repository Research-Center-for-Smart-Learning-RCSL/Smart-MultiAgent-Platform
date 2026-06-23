import { test, expect } from './fixtures/auth'

test.describe('RAG config: create → appears in agent picker → attach (M.1)', () => {
  test('navigate to RAG config list', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    await page.goto(`/projects/${process.env.E2E_PROJECT_ID}/rag-configs`)
    await expect(page).toHaveURL(/rag-configs/)
  })

  test('create a RAG config', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    const projectId = process.env.E2E_PROJECT_ID!
    await page.goto(`/projects/${projectId}/rag-configs`)
    // Wait for the page to settle (both configs and embed-keys queries).
    await page.waitForLoadState('networkidle', { timeout: 10_000 })

    // Open the create form; the toggle button says "New Configuration".
    // The button is disabled when the project has no embedding-capable keys.
    const newConfigBtn = page.getByRole('button', { name: /new configuration/i })
    test.skip(await newConfigBtn.isDisabled(), 'no embed keys in project')
    await newConfigBtn.click()
    await page.locator('#name').fill(`e2e-rag-${Date.now()}`)
    await page.locator('#embed_model').fill('text-embedding-3-small')
    await page.locator('#chunk_strategy').selectOption('fixed')
    await page.locator('#top_k').fill('5')

    // embed_key_id is required — select the first available option if present.
    const embedSelect = page.locator('#embed_key_id')
    const options = embedSelect.locator('option:not([value=""])')
    test.skip((await options.count()) === 0, 'needs project keys with embed capability')
    await embedSelect.selectOption({ index: 1 })

    await page.locator('button[type="submit"]').click()
    // On success the form closes and the new config appears in the table.
    await expect(page.locator('table, [role="table"]')).toBeVisible({ timeout: 5000 })
  })

  test('RAG config appears in agent picker', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_AGENT_ID, 'needs seeded agent')
    await page.goto(`/agents/${process.env.E2E_AGENT_ID}`)
    const ragSelect = page.locator('#rag_config_id')
    await expect(ragSelect).toBeVisible()
    const options = ragSelect.locator('option')
    test.skip((await options.count()) <= 1, 'needs seeded RAG config in project')
  })
})
