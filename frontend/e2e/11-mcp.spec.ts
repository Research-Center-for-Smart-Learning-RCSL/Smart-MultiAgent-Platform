import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

test.describe('MCP: bind server → test → egress allowlist (M.1)', () => {
  test('navigate to agent MCP bindings', async ({ authedPage: page }) => {
    test.skip(!env('E2E_AGENT_ID'), 'needs seeded agent')
    // The standalone /mcp page was folded into the agent Tools page; the old
    // route is kept as a redirect, so the landing URL is /tools.
    await page.goto(`/agents/${env('E2E_AGENT_ID')}/mcp`)
    await expect(page).toHaveURL(/\/agents\/[^/]+\/tools/)
    await expect(page.getByRole('heading', { name: 'MCP Servers' })).toBeVisible()
  })

  test('add an MCP binding', async ({ authedPage: page }) => {
    test.skip(!env('E2E_AGENT_ID'), 'needs seeded agent')
    const agentId = env('E2E_AGENT_ID')!

    // The create's invalidateQueries is deduped into a still-in-flight initial
    // GET, which then resolves with the pre-add list — the binding is created
    // (201) but its row never appears. Same race the egress-allowlist spec
    // below documents; here the list can be empty, so there is no aria-busy
    // table to wait on and the GET itself is the signal. Registered before
    // goto(), since the query fires during the boot this navigation triggers.
    const toolsLoaded = page.waitForResponse(
      (r) =>
        r.request().method() === 'GET' &&
        new URL(r.url()).pathname === `/api/agents/${agentId}/tools`,
      { timeout: 15_000 },
    )
    await page.goto(`/agents/${agentId}/tools`)
    await toolsLoaded

    // "Add Server" appears twice inside the MCP card (header + empty state) and
    // the Functions card has its own "Add Function", so scope to the card and
    // take the header button.
    const mcpCard = page.locator('.s-card').filter({ hasText: 'MCP Servers' })
    await mcpCard.getByRole('button', { name: 'Add Server', exact: true }).first().click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    // SFormField derives each control's id from its `name`, which here is the
    // dotted vee-validate path. The source enum is url | package only.
    // Unique per run: a retry re-adds the binding, so a fixed reference matches
    // the row this attempt created AND the one the failed attempt left behind,
    // and the retry dies on a strict-mode violation instead of reporting the
    // problem it was retrying.
    const reference = `@modelcontextprotocol/server-filesystem-${Date.now()}`
    await page.locator('#config\\.source').selectOption('package')
    await page.locator('#config\\.reference').fill(reference)
    // The form rejects an empty allowlist, so at least one tool name is needed.
    await page.locator('#allowed_tools').fill('read_file')

    // Waiting on the POST distinguishes a completed create from client-side
    // validation that correctly keeps the dialog open.
    const created = page.waitForResponse(
      (r) =>
        r.request().method() === 'POST' &&
        new URL(r.url()).pathname === `/api/agents/${agentId}/tools`,
      { timeout: 15_000 },
    )

    await dialog.getByRole('button', { name: 'Add Server', exact: true }).click()

    const createResp = await created
    expect(createResp.status(), await createResp.text()).toBe(201)

    await expect(dialog).toBeHidden({ timeout: 10_000 })
    await expect(page.getByRole('cell', { name: reference })).toBeVisible()
  })

  test('shows request-validation feedback on the matching MCP field', async ({ authedPage: page }) => {
    test.skip(!env('E2E_AGENT_ID'), 'needs seeded agent')
    const agentId = env('E2E_AGENT_ID')!
    await page.route(`**/api/agents/${agentId}/tools`, async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue()
        return
      }
      await route.fulfill({
        status: 422,
        contentType: 'application/problem+json',
        body: JSON.stringify({
          type: 'https://smap.local/problems/validation',
          title: 'Validation Failed',
          status: 422,
          detail: 'Request validation failed.',
          field_errors: [{ path: 'config.reference', message: 'Package reference is invalid.' }],
        }),
      })
    })

    await page.goto(`/agents/${agentId}/tools`)
    const mcpCard = page.locator('.s-card').filter({ hasText: 'MCP Servers' })
    await mcpCard.getByRole('button', { name: 'Add Server', exact: true }).first().click()
    const dialog = page.getByRole('dialog')
    await page.locator('#config\\.source').selectOption('package')
    await page.locator('#config\\.reference').fill('@invalid/package')
    await page.locator('#allowed_tools').fill('read_file')
    await dialog.getByRole('button', { name: 'Add Server', exact: true }).click()

    await expect(dialog.getByText('Package reference is invalid.')).toBeVisible()
  })

  test('test an MCP binding', async ({ authedPage: page }) => {
    test.skip(!env('E2E_AGENT_ID'), 'needs seeded agent with MCP binding')
    await page.goto(`/agents/${env('E2E_AGENT_ID')}/tools`)
    const testBtn = page
      .locator('main table')
      .getByRole('button', { name: 'Test', exact: true })
      .first()
    // The bindings table fills when the tools query resolves (the previous
    // test created a binding) — wait rather than sampling visibility once.
    let hasBinding = true
    try {
      await testBtn.waitFor({ state: 'visible', timeout: 10_000 })
    } catch {
      hasBinding = false
    }
    test.skip(!hasBinding, 'no MCP binding to test')
    await testBtn.click()

    // There is no inline status element: a successful probe raises a toast, an
    // MCP-level failure opens a detail modal, and a transport failure toasts.
    // Any of the three proves the round trip completed. Discovery boots a
    // sandbox, so allow well past the usual request budget.
    const ok = page.getByText(/Connection OK/)

    // On a gVisor-capable host (staging), E2E_SANDBOX=1 tightens this to
    // success-only: the probe must actually boot the runsc-sandboxed MCP
    // runtime and answer. CI runners have no gVisor and the backend fails
    // closed (SandboxRuntimeViolation), so the default keeps the tri-state
    // acceptance where completing the round trip is the assertion.
    if (process.env.E2E_SANDBOX === '1') {
      await expect(ok).toBeVisible({ timeout: 60_000 })
      return
    }
    const failureModal = page.getByRole('dialog').filter({ hasText: 'MCP Test Failed' })
    const requestFailed = page.getByText('MCP test request failed.')
    await expect(ok.or(failureModal).or(requestFailed)).toBeVisible({ timeout: 30_000 })
  })

  test('add and remove an egress allowlist host', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/mcp/egress-allowlist`)
    const hostname = `e2e-${Date.now()}.example.com`

    // The add form and the per-row remove button render for project owners only.
    await expect(page.locator('#hostname')).toBeVisible()

    // The form renders before the allowlist query settles, so a submit can beat
    // the initial GET. TanStack dedupes the post-add invalidation into that
    // still-in-flight first fetch, which then resolves with the pre-add list and
    // the new row never appears. Wait for the table to leave its loading state.
    await expect(page.locator('table.s-table')).toHaveAttribute('aria-busy', 'false')

    await page.locator('#hostname').fill(hostname)
    await page.locator('#note').fill('E2E test')
    await page.getByRole('button', { name: 'Add', exact: true }).click()
    await expect(page.getByRole('cell', { name: hostname })).toBeVisible()

    // The remove control is an icon-only button named by its aria-label.
    await page.getByRole('row', { name: hostname })
      .getByRole('button', { name: 'Remove', exact: true }).click()
    // SConfirmDialog gates the deletion; it renders as an alertdialog.
    await page.getByRole('alertdialog')
      .getByRole('button', { name: 'Confirm', exact: true }).click()
    await expect(page.getByRole('cell', { name: hostname })).not.toBeVisible({ timeout: 5000 })
  })
})
