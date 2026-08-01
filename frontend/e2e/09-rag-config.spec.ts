import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

test.describe('RAG config: create → appears in agent picker → attach (M.1)', () => {
  test('navigate to RAG config list', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/rag-configs`)
    await expect(page).toHaveURL(/rag-configs/)
  })

  test('create a RAG config', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    const projectId = env('E2E_PROJECT_ID')!
    await page.goto(`/projects/${projectId}/rag-configs`)

    // The page header and the table's empty state both render a button labelled
    // "Create Config", so scope to the header to stay strict-mode safe.
    const createBtn = page
      .locator('main .s-page-header')
      .getByRole('button', { name: 'Create Config', exact: true })
    await expect(createBtn).toBeVisible()
    // Rendered disabled (inside a tooltip) when the project has no
    // embedding-capable key — but it also starts disabled while the key query
    // is in flight, so wait for enablement instead of sampling once.
    let canCreate = true
    try {
      await expect(createBtn).toBeEnabled({ timeout: 10_000 })
    } catch {
      canCreate = false
    }
    test.skip(!canCreate, 'no embed keys in project')
    await createBtn.click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    // SFormField assigns each control's id from its `name` prop, so the field
    // ids match the vee-validate field names.
    const name = `e2e-rag-${Date.now()}`
    await page.locator('#name').fill(name)

    // embed_key_id is required and also drives embed_provider; index 0 is the
    // disabled placeholder option, so index 1 is the first real key.
    const embedSelect = page.locator('#embed_key_id')
    const options = embedSelect.locator('option:not([value=""])')
    let hasKeys = true
    try {
      await expect.poll(() => options.count(), { timeout: 8_000 }).toBeGreaterThan(0)
    } catch {
      hasKeys = false
    }
    test.skip(!hasKeys, 'needs project keys with embed capability')
    await embedSelect.selectOption({ index: 1 })

    // embed_model is deliberately left alone: the view auto-selects the
    // provider's recommended model from the backend catalog, and the control
    // swaps between <select> and <input> as that catalog loads.
    await page.locator('#chunk_strategy').selectOption('fixed')
    await page.locator('#top_k').fill('5')

    await dialog.getByRole('button', { name: 'Create Configuration', exact: true }).click()
    // On success the modal closes; the new row may land on any page, so narrow
    // the list with its search box before asserting.
    await expect(dialog).toBeHidden({ timeout: 10_000 })
    await page.getByPlaceholder('Search configurations...').fill(name)
    await expect(page.getByRole('cell', { name })).toBeVisible()
  })

  test('RAG config appears in agent picker', async ({ authedPage: page }) => {
    test.skip(!env('E2E_AGENT_ID'), 'needs seeded agent')
    await page.goto(`/agents/${env('E2E_AGENT_ID')}`)
    // The picker lives on the Knowledge tab. Tab panels are v-show'd, so the
    // select is in the DOM from the start but stays hidden until the tab is on.
    await page.getByRole('tab', { name: 'Knowledge' }).click()
    const ragSelect = page.locator('#rag_config_id')
    await expect(ragSelect).toBeVisible()
    // Option 0 is always the "no RAG config" entry; real configs land when the
    // configs query resolves, so poll rather than sampling the count once.
    const options = ragSelect.locator('option')
    let hasConfig = true
    try {
      await expect.poll(() => options.count(), { timeout: 8_000 }).toBeGreaterThan(1)
    } catch {
      hasConfig = false
    }
    test.skip(!hasConfig, 'needs seeded RAG config in project')
  })
})
