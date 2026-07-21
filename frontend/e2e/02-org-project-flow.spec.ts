import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

test.describe('Org → invite → accept → transfer OC', () => {
  test('create an org', async ({ authedPage: page }) => {
    await page.goto('/orgs')
    // The name field lives inside OrgListView's SModal (gated by `:open="showCreate"`),
    // so it only exists once the header action opens it. Both the page-header button
    // and the empty-state button render tenancy.org.createTitle = "Create Organization",
    // so scope to the header to stay out of strict-mode trouble.
    await page.locator('.s-page-header').getByRole('button', { name: 'Create Organization' }).click()

    const dialog = page.getByRole('dialog')
    // The field is labelled tenancy.breadcrumb.organizations = "Organizations".
    await dialog.getByRole('textbox', { name: 'Organizations' }).fill('E2E Org')
    await dialog.getByRole('button', { name: 'Create Organization' }).click()

    await expect(page.getByText('E2E Org')).toBeVisible()
  })

  test('create a project under org', async ({ authedPage: page }) => {
    await page.goto('/orgs')
    await page.getByText('E2E Org').click()
    await expect(page.getByRole('heading', { name: /E2E Org/i })).toBeVisible()
  })

  test('invite a member to org', async ({ authedPage: page }) => {
    test.skip(!env('E2E_INVITE_TARGET'), 'needs second user')
    await page.goto('/orgs')
    await page.getByText('E2E Org').click()
    // OrgDetailView renders the Members action as an SButton with as="router-link",
    // i.e. an <a> labelled tenancy.breadcrumb.members = "Members".
    await page.getByRole('link', { name: 'Members' }).click()
    await page.getByLabel('Email').fill(env('E2E_INVITE_TARGET')!)
    // tenancy.member.sendInvite = "Send invite".
    await page.getByRole('button', { name: 'Send invite' }).click()
    // Success toast is tenancy.member.invited = "Invitation sent to {email}".
    await expect(page.getByText(/Invitation sent to/i)).toBeVisible()
  })
})
