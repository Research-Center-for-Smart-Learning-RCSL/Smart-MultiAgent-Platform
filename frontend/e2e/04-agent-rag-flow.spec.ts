import { test, expect } from './fixtures/auth'

test.describe('Create Agent → attach RAG → ingest doc → grounded answer', () => {
  test('create an agent', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    const projectId = process.env.E2E_PROJECT_ID!
    await page.goto(`/projects/${projectId}/agents`)
    await expect(page).toHaveURL(/agents/)

    // Open create form.
    await page.getByRole('button', { name: /create/i }).click()

    // Fill required fields.
    await page.locator('#name').fill(`e2e-agent-${Date.now()}`)
    await page.locator('#model_hint').selectOption('claude')

    // key_group_id auto-selects the first group when only one exists.
    // If no key groups exist, creation is blocked — skip gracefully.
    const keyGroupSelect = page.locator('#key_group_id')
    const keyGroupOptions = keyGroupSelect.locator('option:not([value=""])')
    test.skip((await keyGroupOptions.count()) === 0, 'needs at least one key group')

    await page.getByRole('button', { name: /submit|create|save/i }).last().click()

    // Agent should appear in the list.
    await expect(page.locator('.agent-list__items li').first()).toBeVisible({ timeout: 5000 })
  })

  test('configure RAG on agent', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_AGENT_ID, 'needs seeded agent')
    await page.goto(`/agents/${process.env.E2E_AGENT_ID}`)

    // The detail page should show RAG config picker.
    const ragSelect = page.locator('#rag_config_id')
    await expect(ragSelect).toBeVisible({ timeout: 10_000 })

    // Select a RAG config if available.
    const options = ragSelect.locator('option:not([value=""])')
    test.skip((await options.count()) === 0, 'needs at least one RAG config')
    await ragSelect.selectOption({ index: 1 })

    // Save changes.
    await page.getByRole('button', { name: /save|update|submit/i }).click()
    await expect(page.locator('[role="status"]').or(page.getByText(/saved|updated|success/i))).toBeVisible({
      timeout: 5000,
    })
  })

  test('navigate to RAG ingest', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    const projectId = process.env.E2E_PROJECT_ID!
    await page.goto(`/projects/${projectId}/rag-configs`)
    await expect(page).toHaveURL(/rag-configs/)

    // Click into the first RAG config if one exists.
    const firstLink = page.locator('table a, [role="table"] a').first()
    test.skip(!(await firstLink.isVisible().catch(() => false)), 'no RAG configs')
    await firstLink.click()
    await expect(page).toHaveURL(/rag-configs\//)
  })
})
