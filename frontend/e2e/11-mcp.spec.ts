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
    await page.goto(`/agents/${env('E2E_AGENT_ID')}/tools`)

    // "Add Server" appears twice inside the MCP card (header + empty state) and
    // the Functions card has its own "Add Function", so scope to the card and
    // take the header button.
    const mcpCard = page.locator('.s-card').filter({ hasText: 'MCP Servers' })
    await mcpCard.getByRole('button', { name: 'Add Server', exact: true }).first().click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    // SFormField derives each control's id from its `name`, which here is the
    // dotted vee-validate path. The source enum is url | package only.
    const reference = '@modelcontextprotocol/server-filesystem'
    await page.locator('#config\\.source').selectOption('package')
    await page.locator('#config\\.reference').fill(reference)
    // The form rejects an empty allowlist, so at least one tool name is needed.
    await page.locator('#allowed_tools').fill('read_file')

    await dialog.getByRole('button', { name: 'Add Server', exact: true }).click()
    await expect(dialog).toBeHidden({ timeout: 10_000 })
    await expect(page.getByRole('cell', { name: reference })).toBeVisible()
  })

  test('test an MCP binding', async ({ authedPage: page }) => {
    test.skip(!env('E2E_AGENT_ID'), 'needs seeded agent with MCP binding')
    await page.goto(`/agents/${env('E2E_AGENT_ID')}/tools`)
    const testBtn = page
      .locator('main table')
      .getByRole('button', { name: 'Test', exact: true })
      .first()
    test.skip(!(await testBtn.isVisible().catch(() => false)), 'no MCP binding to test')
    await testBtn.click()

    // There is no inline status element: a successful probe raises a toast, an
    // MCP-level failure opens a detail modal, and a transport failure toasts.
    // Any of the three proves the round trip completed. Discovery boots a
    // sandbox, so allow well past the usual request budget.
    const ok = page.getByText(/Connection OK/)
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
