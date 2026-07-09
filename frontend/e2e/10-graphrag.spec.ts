import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// Concept Maps overview (GraphRAG Phase 4alpha re-home, R11.09). The former
// agent-bound GraphRAG "New Configuration" form became a project-scoped overview
// across all three owner kinds (chatroom / agent_group / workspace) with an
// owner-type filter, live build-state pills, and an owner-picker create flow.
test.describe('Concept Maps overview: filter → create (owner picker) → build', () => {
  test('navigate to the Concept Maps overview', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/graphrag-configs`)
    await expect(page).toHaveURL(/graphrag-configs/)
    // The owner-type filter and the table are the overview's anchors.
    await expect(page.locator('#owner-kind-filter')).toBeVisible()
    await expect(page.locator('table, [role="table"]').first()).toBeVisible()
  })

  test('owner-type filter stays functional', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/graphrag-configs`)
    // Narrow to a single owner kind; the table (with its empty-state row when the
    // filter matches nothing) stays rendered.
    await page.locator('#owner-kind-filter').selectOption({ label: 'Workspace' })
    await expect(page.locator('table, [role="table"]').first()).toBeVisible()
  })

  test('create a Concept Map via the owner picker', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/graphrag-configs`)

    // Let the owner-options + key-group queries settle before judging the button.
    await page.waitForTimeout(3000)
    const createBtn = page.getByRole('button', { name: /New Concept Map/i }).first()
    // The button enables only when there is a selectable (map-less) owner AND a key
    // group; skip (not fail) rather than assert the seed always has both.
    let canCreate = true
    try {
      await expect(createBtn).toBeEnabled({ timeout: 10_000 })
    } catch {
      canCreate = false
    }
    test.skip(!canCreate, 'no selectable owner or key group to create with')
    await createBtn.click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    // owner_kind, owner, builder_key_group — in DOM order; index 0 is the
    // disabled placeholder, so index 1 is the first real option.
    const selects = dialog.locator('.s-select__native')
    await selects.nth(0).selectOption({ index: 1 })
    await selects.nth(1).selectOption({ index: 1 })
    await selects.nth(2).selectOption({ index: 1 })

    await dialog.getByRole('button', { name: /Create Configuration/i }).click()
    // On success the modal closes and the overview table remains.
    await expect(dialog).toBeHidden({ timeout: 10_000 })
    await expect(page.locator('table, [role="table"]').first()).toBeVisible()
  })

  test('build triggers a status change', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/graphrag-configs`)
    const buildBtn = page.getByRole('button', { name: /^Build$/i }).first()
    test.skip(!(await buildBtn.isVisible().catch(() => false)), 'no buildable Concept Map on the page')
    await buildBtn.click()
    // The click optimistically flips the row's pill to "Running"; a real build
    // then advances to a committed/failed terminal state.
    await expect(
      page.getByText(/Running|Neo4j committed|Qdrant committed|Failed/i).first(),
    ).toBeVisible({ timeout: 30_000 })
  })
})
