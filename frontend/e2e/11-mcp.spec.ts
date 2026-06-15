import { test, expect } from './fixtures/auth'

test.describe('MCP: bind server → test → egress allowlist (M.1)', () => {
  test('navigate to agent MCP bindings', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_AGENT_ID, 'needs seeded agent')
    await page.goto(`/agents/${process.env.E2E_AGENT_ID}/mcp`)
    await expect(page).toHaveURL(/mcp/)
  })

  test('add a builtin MCP binding', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_AGENT_ID, 'needs seeded agent')
    await page.goto(`/agents/${process.env.E2E_AGENT_ID}/mcp`)
    await page.getByRole('button', { name: /add/i }).click()

    await page.locator('#source').selectOption('builtin')
    const refSelect = page.locator('select#reference')
    await expect(refSelect).toBeVisible()
    await refSelect.selectOption({ index: 1 })
    await page.locator('button[type="submit"], .btn-primary').click()
    await expect(page.locator('table, [role="table"]')).toBeVisible()
  })

  test('test an MCP binding', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_AGENT_ID, 'needs seeded agent with MCP binding')
    await page.goto(`/agents/${process.env.E2E_AGENT_ID}/mcp`)
    const testBtn = page.getByRole('button', { name: /test/i }).first()
    test.skip(!(await testBtn.isVisible()), 'no MCP binding to test')
    await testBtn.click()
    await expect(
      page.locator('.agent-mcp__ok, .agent-mcp__error'),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('add and remove an egress allowlist host', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    await page.goto(`/projects/${process.env.E2E_PROJECT_ID}/mcp/egress-allowlist`)
    const hostname = `e2e-${Date.now()}.example.com`

    await page.locator('#hostname').fill(hostname)
    await page.locator('#note').fill('E2E test')
    await page.getByRole('button', { name: /add/i }).click()
    await expect(page.getByText(hostname)).toBeVisible()

    await page.getByRole('row', { name: hostname })
      .getByRole('button', { name: /remove/i }).click()
    await expect(page.getByText(hostname)).not.toBeVisible()
  })
})
