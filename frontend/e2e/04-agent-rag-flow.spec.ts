import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

test.describe('Create Agent → attach RAG → ingest doc → grounded answer', () => {
  test('create an agent', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    const projectId = env('E2E_PROJECT_ID')!
    await page.goto(`/projects/${projectId}/agents`)
    await expect(page).toHaveURL(/agents/)

    // Toggle button text is "New Agent" (i18n: agents.list.create).
    await page.getByRole('button', { name: /new agent/i }).click()

    // key_group_id auto-selects the first group when only one exists.
    // If no key groups exist, creation is blocked — skip before filling the form.
    const keyGroupSelect = page.locator('#key_group_id')
    const keyGroupOptions = keyGroupSelect.locator('option:not([value=""])')
    test.skip((await keyGroupOptions.count()) === 0, 'needs at least one key group')

    await page.locator('#name').fill(`e2e-agent-${Date.now()}`)
    await page.locator('#model_hint').selectOption('claude')

    await page.getByRole('button', { name: /submit|create|save/i }).last().click()

    // Agent should appear in the list.
    await expect(page.locator('.agent-list__items li').first()).toBeVisible({ timeout: 5000 })
  })

  test('configure RAG on agent', async ({ authedPage: page }) => {
    test.skip(!env('E2E_AGENT_ID'), 'needs seeded agent')
    await page.goto(`/agents/${env('E2E_AGENT_ID')}`)

    // Wait for the agent detail form to load (async query).
    const ragSelect = page.locator('#rag_config_id')
    await expect(ragSelect).toBeVisible({ timeout: 10_000 })

    const options = ragSelect.locator('option:not([value=""])')
    test.skip((await options.count()) === 0, 'needs at least one RAG config')
    await ragSelect.selectOption({ index: 1 })

    await page.getByRole('button', { name: /save|update|submit/i }).click()
    await expect(page.locator('[role="status"]').or(page.getByText(/saved|updated|success/i))).toBeVisible({
      timeout: 5000,
    })
  })

  test('navigate to RAG ingest', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    const projectId = env('E2E_PROJECT_ID')!
    await page.goto(`/projects/${projectId}/rag-configs`)
    await expect(page).toHaveURL(/rag-configs/)

    // Wait for the table to render after async data loads.
    const table = page.locator('table, [role="table"]')
    await expect(table).toBeVisible({ timeout: 10_000 })
    const firstLink = table.locator('a').first()
    test.skip((await firstLink.count()) === 0, 'no RAG configs')
    await firstLink.click()
    await expect(page).toHaveURL(/rag-configs\//)
  })
})
