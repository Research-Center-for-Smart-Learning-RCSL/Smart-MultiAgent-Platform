import { test, expect, seedAdmin } from './fixtures/auth'
import { env } from './fixtures/seed'

// Onboarding without SMTP (R6.09, R6.10, R6.18). Copy resolved from the locale
// bundles:
//   tenancy.member.sendInvite      -> "Send invite"
//   tenancy.member.invitePickLabel -> "Organization member"
//   tenancy.invite.acceptSuccess   -> "Invitation accepted!"
//   admin.users.create             -> "Create user"
//   admin.users.createSubmit       -> "Create account"
//   admin.users.linksTitle         -> "Activation links"
//   common.copied                  -> "Copied"
//
// Located by the SFormField-assigned input id (`name` prop) rather than by
// label: SCopyField's copy button carries an aria-label of "<label>: Copy" so a
// screen reader can tell two fields apart, which makes getByLabel('Accept
// link') match the button as well as the input.
//
// The point of the whole feature is that no mail server is involved, so every
// assertion is about what the inviter's own screen carries: nothing here reads
// a mailbox.

const ORG_ID = env('E2E_ORG_ID')
const PROJECT_ID = env('E2E_PROJECT_ID')

test.describe('Onboarding without SMTP', () => {
  test('an org invite returns an accept link a second person can redeem', async ({
    authedPage: page,
    adminPage,
  }) => {
    test.skip(!ORG_ID, 'needs a seeded org')
    // The copy button writes through the real Clipboard API; without this the
    // write is refused and the button never reaches its copied state.
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])

    await page.goto(`/orgs/${ORG_ID}/members`)
    await page.locator('#inviteEmail').fill(seedAdmin.email)
    await page.getByRole('button', { name: 'Send invite' }).click()

    const linkCard = page.locator('.invite-link')
    await expect(linkCard).toBeVisible({ timeout: 15_000 })

    const acceptUrl = await page.locator('#inviteAcceptUrl').inputValue()
    // The token rides in the URL fragment so it never reaches a server log, a
    // Referer, or browser history (SEC-8).
    expect(acceptUrl).toContain('#token=')

    await linkCard.locator('.s-copy-field__button').click()
    await expect(linkCard.locator('.s-copy-field__button')).toHaveText('Copied')

    // Navigate the *path and fragment* on the test origin rather than the
    // absolute URL: the backend builds it from its own configured public
    // origin, which is not necessarily the Vite dev server this suite drives.
    // The forwarding under test — Landing.vue handing `?invite=1#token=` to
    // /invites/accept — is exercised either way.
    const forwarded = new URL(acceptUrl)
    await adminPage.goto(`${forwarded.pathname}${forwarded.search}${forwarded.hash}`)
    await expect(adminPage.getByText('Invitation accepted!')).toBeVisible({ timeout: 20_000 })
  })

  test('a project invite picks from the parent org instead of asking for an address', async ({
    authedPage: page,
  }) => {
    test.skip(!PROJECT_ID, 'needs a seeded project')
    // Depends on the previous test: the pool is the parent Org's members, and
    // the admin only joined it by accepting there. workers:1 plus
    // fullyParallel:false makes that ordering real.
    await page.goto(`/projects/${PROJECT_ID}/members`)

    const picker = page.locator('#invitePickedUser')
    await expect(picker).toBeVisible({ timeout: 15_000 })
    await expect(picker.locator(`option:text-is("${seedAdmin.email}")`)).toHaveCount(1)
    // The picker replaces the typed field rather than sitting beside it.
    await expect(page.locator('#inviteEmail')).toHaveCount(0)

    await picker.selectOption({ label: seedAdmin.email })
    await page.getByRole('button', { name: 'Send invite' }).click()

    const linkCard = page.locator('.invite-link')
    await expect(linkCard).toBeVisible({ timeout: 15_000 })
    await expect(linkCard).toContainText(seedAdmin.email)
    expect(await page.locator('#inviteAcceptUrl').inputValue()).toContain('#token=')

    // The invitee leaves the pool the moment the invite exists, so the picker
    // cannot offer them twice and hit the duplicate error it exists to avoid.
    await page.reload()
    await expect(page.locator(`option:text-is("${seedAdmin.email}")`)).toHaveCount(0, {
      timeout: 15_000,
    })
  })

  test('an admin provisions an account and gets two distinct activation links', async ({
    adminPage: page,
  }) => {
    await page.goto('/admin/users')
    await page.locator('.s-page-header__actions').getByRole('button', { name: 'Create user' }).click()

    const address = `e2e-provisioned-${Date.now()}@example.com`
    await page.locator('#adminCreateUserEmail').fill(address)
    await page.getByRole('button', { name: 'Create account' }).click()

    await expect(page.locator('#adminSetPasswordUrl')).toBeVisible({ timeout: 15_000 })
    const setPassword = await page.locator('#adminSetPasswordUrl').inputValue()
    const verifyEmail = await page.locator('#adminVerifyEmailUrl').inputValue()

    // Handing over the wrong one is the obvious failure mode, so the two must
    // be separately labelled and genuinely different.
    expect(setPassword).not.toBe(verifyEmail)
    expect(setPassword).toContain('#token=')
    expect(verifyEmail).toContain('#token=')
    // No plaintext password anywhere — there is none: the account holder sets
    // their own through the first link.
    await expect(page.locator('.s-modal__panel input[type="password"]')).toHaveCount(0)

    // The provisioned account shows up unverified and pending, exactly as a
    // self-registered one would: R6.02's gates are untouched (Q-3).
    await page.locator('.s-modal__footer').getByRole('button').last().click()
    await page.locator('.admin-users__search input').fill(address)
    // `exact` matters: SSearchInput's own clear button is named "Clear search",
    // which a substring match on "Search" also resolves.
    await page.locator('.admin-users__filters')
      .getByRole('button', { name: 'Search', exact: true })
      .click()
    const row = page.locator('tbody tr', { hasText: address })
    await expect(row).toBeVisible({ timeout: 15_000 })
    await expect(row).toContainText('Pending')
  })
})
