import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// STable marks every *data* row with `s-table__row--clickable`; the empty-state
// row is a plain <tr>. It is the only way to tell "no rows" from "one empty-state
// row" here, since both are `tbody tr` and neither carries a distinct role.
const DATA_ROW = '.s-table__row--clickable'

test.describe('Create Agent → attach RAG → ingest doc → grounded answer', () => {
  test('create an agent', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    const projectId = env('E2E_PROJECT_ID')!
    const agentName = `e2e-agent-${Date.now()}`

    await page.goto(`/projects/${projectId}/agents`)
    await expect(page).toHaveURL(/agents/)

    // agents.list.create = "Create AI Agent". It is not an in-place form toggle:
    // it routes to AgentDetailView in create mode (/agents/new?projectId=...),
    // which is where the form fields live.
    await page.locator('.s-page-header').getByRole('button', { name: 'Create AI Agent' }).click()
    await page.waitForURL(/\/agents\/new/)

    // SFormField assigns its `name` as the id of the control it wraps, so the
    // General tab's fields resolve as #name / #model_hint / #key_group_id.
    const keyGroupSelect = page.locator('#key_group_id')
    await expect(keyGroupSelect).toBeVisible({ timeout: 10_000 })

    // Select the SEEDED group explicitly rather than accepting the default.
    // AgentDetailView defaults key_group_id to the first group, and the backend
    // orders key groups created_at DESC, so any group created later by another
    // spec (03 creates one when the list looks empty) sorts ahead of the seeded
    // one. That group carries no key, so the create would 422 with
    // key-group-no-matching-provider — the flake this guards against.
    const keyGroupId = env('E2E_KEY_GROUP_ID')
    test.skip(!keyGroupId, 'needs seeded key group')

    // The options arrive asynchronously, so wait for the seeded one specifically
    // rather than for whichever option lands first.
    await keyGroupSelect
      .locator(`option[value="${keyGroupId}"]`)
      .waitFor({ state: 'attached', timeout: 10_000 })
    await keyGroupSelect.selectOption(keyGroupId!)

    await page.locator('#name').fill(agentName)
    // model_hint must match a provider actually carried into the selected key
    // group (agent_service._assert_key_group_has_provider), else the create
    // 422s. The seeded group holds an OpenAI key only.
    await page.locator('#model_hint').selectOption('openai')

    // The create POST is ground truth, the same way the login fixture treats the
    // login response: a rejected create leaves the form in place with an inline
    // field error and no navigation, so asserting only on the URL turns every
    // backend rejection — and every request that never left the page — into an
    // opaque 60s timeout that says nothing about which of the two happened.
    const created = page.waitForResponse(
      (r) =>
        r.request().method() === 'POST' &&
        new URL(r.url()).pathname === `/api/projects/${projectId}/agents`,
      { timeout: 20_000 },
    )

    // agents.detail.save = "Save Changes" (page header; the duplicate in the
    // mobile bottom bar is v-if="isMobile" and absent at desktop viewports).
    await page.locator('.s-page-header').getByRole('button', { name: 'Save Changes' }).click()

    const createResp = await created
    expect(createResp.status(), await createResp.text()).toBe(201)

    // On success AgentDetailView replaces the route with the created agent's id.
    await page.waitForURL((url) => /^\/agents\/[^/]+$/.test(url.pathname) && !url.pathname.endsWith('/new'))

    // Agent should appear in the list.
    await page.goto(`/projects/${projectId}/agents`)
    await expect(page.getByRole('cell', { name: agentName })).toBeVisible({ timeout: 10_000 })
  })

  test('configure RAG on agent', async ({ authedPage: page }) => {
    test.skip(!env('E2E_AGENT_ID'), 'needs seeded agent')
    await page.goto(`/agents/${env('E2E_AGENT_ID')}`)

    // rag_config_id lives on the Knowledge tab, which is `v-show`-hidden until
    // that tab is selected (agents.detail.tabs.knowledge = "Knowledge").
    await page.getByRole('tab', { name: 'Knowledge' }).click({ timeout: 10_000 })

    const ragSelect = page.locator('#rag_config_id')
    await expect(ragSelect).toBeVisible({ timeout: 10_000 })

    // Option 0 is agents.form.noRagConfig (value=""), so any option beyond it is
    // a real config.
    const options = ragSelect.locator('option:not([value=""])')
    test.skip((await options.count()) === 0, 'needs at least one RAG config')
    await ragSelect.selectOption({ index: 1 })

    await page.locator('.s-page-header').getByRole('button', { name: 'Save Changes' }).click()
    // Success toast is agents.detail.saved.
    await expect(page.getByText('AI Agent saved.')).toBeVisible({ timeout: 10_000 })
  })

  test('navigate to RAG ingest', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    const projectId = env('E2E_PROJECT_ID')!
    await page.goto(`/projects/${projectId}/rag-configs`)
    await expect(page).toHaveURL(/rag-configs/)

    // Wait for the table to render after async data loads.
    const table = page.getByRole('table')
    await expect(table).toBeVisible({ timeout: 10_000 })

    // RagConfigListView renders the name cell as a plain <span>, not a link —
    // navigation to a config is the STable row click.
    const firstRow = table.locator(DATA_ROW).first()
    test.skip((await table.locator(DATA_ROW).count()) === 0, 'no RAG configs')
    await firstRow.click()
    await expect(page).toHaveURL(/rag-configs\//)
  })
})
