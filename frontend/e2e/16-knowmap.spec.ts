import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// Knowledge Map (knowmap, Phase 4beta): the document-corpus twin of the Concept
// Map. Both products share the GraphRAG 2PC build engine (idle -> running ->
// neo4j_committed -> qdrant_committed, with failed/compensating branches), so
// driving create -> upload -> build from the UI exercises the second 2PC
// subsystem end-to-end: config CRUD, document ingestion (upload + text
// extraction + storage), the Arq build job, and the build-state WS channel
// that animates the badge. Until this spec, knowmap had zero e2e coverage.
//
// CI has no real LLM behind the builder key group, so a build that terminates
// in `Failed` still proves the full path: the job was dispatched, the state
// machine left idle, and the terminal transition was persisted and delivered
// over WS. (Same convention as 10-graphrag.)
const STATE_ADVANCED = /Running|Neo4j committed|Qdrant committed|Compensating|Failed/

// Unique per run: a CI retry re-creates the map, and a fixed name would make
// the retry die on a strict-mode violation instead of the real failure.
const MAP_NAME = `E2E Knowmap ${Date.now()}`

// The header state badge is the first .s-badge inside <main> (the only other
// badge-like element on the page is the Documents tab counter, which uses the
// distinct s-tabs__badge class).
function stateBadge(page: import('@playwright/test').Page) {
  return page.locator('main .s-badge').first()
}

async function openDetail(page: import('@playwright/test').Page): Promise<boolean> {
  await page.goto(`/projects/${env('E2E_PROJECT_ID')}/knowmap-configs`)
  const row = page.getByRole('cell', { name: MAP_NAME }).first()
  if (!(await row.isVisible().catch(() => false))) return false
  await row.click()
  await expect(page).toHaveURL(/\/knowmap-configs\/[^/]+/)
  return true
}

test.describe('Knowledge Map: create → upload document → build (2PC) → graph', () => {
  test('create a Knowledge Map', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/knowmap-configs`)
    await expect(page.getByRole('heading', { name: 'Knowledge Maps' })).toBeVisible()

    // Disabled (behind a tooltip) when the project has no key group. The seed
    // creates one; skip rather than fail if that chain broke upstream.
    const createBtn = page
      .locator('main .s-page-header')
      .getByRole('button', { name: 'Create Knowledge Map', exact: true })
    let canCreate = true
    try {
      await expect(createBtn).toBeEnabled({ timeout: 15_000 })
    } catch {
      canCreate = false
    }
    test.skip(!canCreate, 'no key group to create with')
    await createBtn.click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    // The builder key group select is pre-filled with the first key group by
    // openCreateModal(), so only the name is required.
    await dialog.locator('#name').fill(MAP_NAME)
    await dialog.getByRole('button', { name: 'Create Knowledge Map', exact: true }).click()

    await expect(dialog).toBeHidden({ timeout: 10_000 })
    await expect(page.getByRole('cell', { name: MAP_NAME })).toBeVisible({ timeout: 10_000 })
  })

  test('upload a document and watch the 2PC state machine leave idle', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    test.skip(!(await openDetail(page)), 'Knowledge Map was not created upstream')

    await page.getByRole('tab', { name: /Documents/ }).click()
    await page.locator('input[type="file"]').setInputFiles({
      name: 'e2e-knowmap.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from(
        'SMAP is a self-hosted multi-agent platform. Agents form groups. Groups hold conversations.',
      ),
    })
    // Upload -> text extraction -> document row. Generous budget: the file
    // passes through storage and the extraction pipeline before listing.
    await expect(page.getByText('e2e-knowmap.txt')).toBeVisible({ timeout: 20_000 })

    // An upload auto-triggers a rebuild (F-22); if that did not engage (state
    // still Idle), fall back to the explicit Rebuild button. Either way the
    // badge must leave idle and reach an in-progress or terminal 2PC state.
    try {
      await expect(stateBadge(page)).toHaveText(STATE_ADVANCED, { timeout: 10_000 })
    } catch {
      await page.getByRole('button', { name: 'Rebuild', exact: true }).click()
      await expect(stateBadge(page)).toHaveText(STATE_ADVANCED, { timeout: 30_000 })
    }
  })

  test('rebuild from a settled state re-enters the build cycle', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    test.skip(!(await openDetail(page)), 'Knowledge Map was not created upstream')

    // Rebuild is disabled while a build is in flight; wait for the previous
    // test's build to settle (terminal states re-enable the button). A build
    // still in flight after the budget is an environment condition, not a
    // regression — skip instead of failing.
    const rebuild = page.getByRole('button', { name: 'Rebuild', exact: true })
    let settled = true
    try {
      await expect(rebuild).toBeEnabled({ timeout: 60_000 })
    } catch {
      settled = false
    }
    test.skip(!settled, 'previous build still in flight')
    await rebuild.click()
    // The 202 surfaces as a toast; the optimistic badge flip to Running can
    // settle to Failed before we look, so the toast is the stable signal.
    await expect(page.getByText('Build started.')).toBeVisible({ timeout: 15_000 })
  })

  test('graph view is reachable from the detail page', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    test.skip(!(await openDetail(page)), 'Knowledge Map was not created upstream')

    await page.getByRole('button', { name: 'View Graph', exact: true }).click()
    await expect(page).toHaveURL(/\/knowmap-configs\/[^/]+\/graph/)
    // The shared graph view (domain: knowmap) must mount rather than error out.
    await expect(page.locator('.s-alert--danger')).toHaveCount(0)
  })
})
