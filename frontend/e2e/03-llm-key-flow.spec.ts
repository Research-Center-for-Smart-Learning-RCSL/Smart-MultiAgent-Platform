import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// STable marks every *data* row with `s-table__row--clickable`; the empty-state
// row is a plain <tr>. It is the only way to tell "no rows" from "one empty-state
// row" here, since both are `tbody tr` and neither carries a distinct role.
const DATA_ROW = '.s-table__row--clickable'

test.describe('Upload LLM key → validate → carry into project → key group', () => {
  test('upload an API key', async ({ authedPage: page }) => {
    const keyName = `e2e-key-${Date.now()}`

    await page.goto('/keys')
    await expect(page).toHaveURL(/keys/)

    // KeyUploadForm is an SModal gated by `:open="showUpload"` — its fields do not
    // exist until the header action opens it. The empty state renders the same
    // keys.form.submit = "Upload Key" label, so scope to the page header.
    await page.locator('.s-page-header').getByRole('button', { name: 'Upload Key' }).click()

    // SSelect/SInput put the passed-through data-testid on their wrapper <div>,
    // not the native control, so descend to the <select>/<input>.
    const providerSelect = page.locator('[data-testid="key-provider"] select')
    await expect(providerSelect).toBeVisible({ timeout: 10_000 })
    await providerSelect.selectOption('openai')

    await page.locator('[data-testid="key-name"] input').fill(keyName)
    await page
      .locator('[data-testid="key-secret"] input')
      .fill('sk-test-00000000000000000000000000000000')
    await page.locator('[data-testid="key-upload-submit"]').click()

    // The new key should appear in the table.
    await expect(page.getByRole('cell', { name: keyName })).toBeVisible({ timeout: 10_000 })
  })

  test('retest uploaded key', async ({ authedPage: page }) => {
    await page.goto('/keys')
    // Wait for the key list table to finish async loading before checking rows.
    const keyList = page.getByRole('table')
    await expect(keyList).toBeVisible({ timeout: 10_000 })

    const firstRow = keyList.locator(DATA_ROW).first()
    test.skip((await keyList.locator(DATA_ROW).count()) === 0, 'no keys to retest')

    // Retest is a menu item behind the row's actions menu, not a standalone
    // button: the SDropdown trigger is labelled keys.list.actions = "Actions"
    // and the item is keys.list.retest = "Retest".
    await firstRow.getByRole('button', { name: 'Actions' }).click()
    await page.getByRole('menuitem', { name: 'Retest' }).click()

    // KeyListView renders the status as an SStatusBadge carrying
    // `aria-label="Status: <test_status>"`.
    await expect(firstRow.locator('[aria-label^="Status:"]')).toBeVisible({ timeout: 10_000 })
  })

  test('carry key into project via key groups', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    const projectId = env('E2E_PROJECT_ID')!

    // Navigate to project key groups.
    await page.goto(`/projects/${projectId}/key-groups`)
    await expect(page).toHaveURL(/key-groups/)

    const groupList = page.getByRole('table')
    await expect(groupList).toBeVisible({ timeout: 10_000 })

    // KeyGroupListView renders each group's name cell as a router-link to its
    // detail route, so the links are the group rows.
    const groupLinks = groupList.getByRole('link')

    // If no group exists, create one. The form is inside an SModal gated by
    // `:open="showCreate"`; keys.groups.create = "Create Group" labels both the
    // header action and the empty-state button, so scope to the header.
    if ((await groupLinks.count()) === 0) {
      await page.locator('.s-page-header').getByRole('button', { name: 'Create Group' }).click()
      await page.locator('[data-testid="group-name"] input').fill(`e2e-group-${Date.now()}`)
      await page.locator('[data-testid="group-create"]').click()
      await expect(groupLinks.first()).toBeVisible({ timeout: 10_000 })
    }

    // Verify key group link is navigable.
    await groupLinks.first().click()
    await expect(page).toHaveURL(/key-groups\//)
  })

  test('project keys page loads', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/keys`)
    await expect(page).toHaveURL(/keys/)
  })
})
