import type { Page } from '@playwright/test'
import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// Org and project detail share the same inline-rename markup: a "Rename" button
// in the page header swaps in a <form class="rename-form"> holding the name
// input plus "Save"/"Cancel". The name is also echoed in the breadcrumb, so
// assertions target the <h1> via the heading role rather than getByText, which
// would match both.
async function inlineRename(page: Page, from: string, to: string) {
  await page.getByRole('button', { name: 'Rename', exact: true }).click()
  const form = page.locator('.rename-form')
  await expect(form).toBeVisible()
  await expect(form.locator('input')).toHaveValue(from)
  await form.locator('input').fill(to)
  await form.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByRole('heading', { name: to, level: 1 })).toBeVisible({ timeout: 5000 })
}

test.describe('Tenancy/keys mgmt: roles, rename, key-group, usage (M.4)', () => {
  test('change a project member role', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project with members')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/members`)
    await expect(page).toHaveURL(/members/)

    // There is no promote/demote button: the role actions are SDropdown
    // menuitems labelled with the target role (tenancy.role.owner "Owner" /
    // tenancy.role.member "Member"), and useMemberActions returns no actions at
    // all for yourself, the original creator, or an inherited org member — so
    // the row menu only exists once the project has another direct member.
    const rowMenus = page.locator('.s-table__row .s-dropdown__trigger button')
    test.skip((await rowMenus.count()) === 0, 'no member with role actions')

    await rowMenus.first().click()
    const menu = page.getByRole('menu')
    await expect(menu).toBeVisible()
    await expect(
      menu
        .getByRole('menuitem', { name: 'Owner', exact: true })
        .or(menu.getByRole('menuitem', { name: 'Member', exact: true })),
    ).toBeVisible({ timeout: 5000 })
  })

  test('rename a project', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}`)

    const heading = page.getByRole('heading', { level: 1 })
    await expect(heading).toBeVisible()
    const original = (await heading.textContent())!.trim()

    await inlineRename(page, original, `${original}-r`)
    // Restore original name.
    await inlineRename(page, `${original}-r`, original)
  })

  test('rename an org', async ({ authedPage: page }) => {
    test.skip(!env('E2E_ORG_ID'), 'needs seeded org')
    await page.goto(`/orgs/${env('E2E_ORG_ID')}`)

    const heading = page.getByRole('heading', { level: 1 })
    await expect(heading).toBeVisible()
    const original = (await heading.textContent())!.trim()

    await inlineRename(page, original, `${original}-r`)
    // Restore original name.
    await inlineRename(page, `${original}-r`, original)
  })

  test('key group rename', async ({ authedPage: page }) => {
    test.skip(!env('E2E_KEY_GROUP_URL'), 'needs seeded key group (projectId/key-groups/id)')
    await page.goto(`/${env('E2E_KEY_GROUP_URL')}`)

    // KeyGroupDetailView uses its own rename affordance, not the tenancy one:
    // an icon-only pencil button (aria-label keys.groups.rename "Rename") swaps
    // the <h1> for a bare SInput — no <form>, and the commit/cancel buttons are
    // icon-only with no accessible name. The input's own Enter handler is the
    // only labelled way to save.
    const heading = page.getByRole('heading', { level: 1 })
    await expect(heading).toBeVisible()
    const original = (await heading.textContent())!.trim()

    const renameBtn = page.getByRole('button', { name: 'Rename', exact: true })
    await expect(renameBtn).toBeVisible()
    await renameBtn.click()

    const nameInput = page.locator('.s-page-header input')
    await expect(nameInput).toHaveValue(original)
    await nameInput.fill(`${original}-r`)
    await nameInput.press('Enter')
    await expect(
      page.getByRole('heading', { name: `${original}-r`, level: 1 }),
    ).toBeVisible({ timeout: 5000 })

    // Restore original name.
    await page.getByRole('button', { name: 'Rename', exact: true }).click()
    await page.locator('.s-page-header input').fill(original)
    await page.locator('.s-page-header input').press('Enter')
    await expect(page.getByRole('heading', { name: original, level: 1 })).toBeVisible({ timeout: 5000 })
  })

  test('project key usage panel renders', async ({ authedPage: page }) => {
    test.skip(!env('E2E_PROJECT_ID'), 'needs seeded project with carried keys')
    await page.goto(`/projects/${env('E2E_PROJECT_ID')}/keys`)
    await expect(page).toHaveURL(/keys/)

    // The per-row usage toggle is an icon-only button labelled
    // keys.project.usage ("Usage"); the column header of the same name is a
    // <th>, so the button role keeps this unambiguous.
    const usageBtn = page.getByRole('button', { name: 'Usage', exact: true })
    test.skip((await usageBtn.count()) === 0, 'no carried keys')

    await usageBtn.first().click()
    // UsageDashboard's stat tiles: Requests / Input Tokens / Output Tokens /
    // Errors.
    await expect(page.getByText('Requests', { exact: true })).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('Errors', { exact: true })).toBeVisible()
  })

  test('org quotas display', async ({ authedPage: page }) => {
    test.skip(!env('E2E_ORG_ID'), 'needs seeded org')
    await page.goto(`/orgs/${env('E2E_ORG_ID')}`)
    // Quotas card loads non-blocking and is v-if'd on the quotas query, so wait
    // on its heading (tenancy.quotas.title "Quotas") rather than the page body.
    await expect(
      page.getByRole('heading', { name: 'Quotas', exact: true }),
    ).toBeVisible({ timeout: 5000 })
  })
})
