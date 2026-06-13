import { test as base, type Page } from '@playwright/test'

export interface TestUser {
  email: string
  password: string
}

// NB: a non-reserved email domain — the auth endpoints validate with pydantic
// EmailStr, which rejects reserved TLDs like `.test` with 422 (the seed user is
// inserted via the repository, bypassing that check, so it could be created but
// never logged in). Keep this in sync with SMAP_SEED_*_EMAIL in compose.test.yml.
export const seedUser: TestUser = {
  email: 'e2e-user@example.com',
  password: 'E2eP@ssw0rd!Str0ng',
}

export const seedAdmin: TestUser = {
  email: 'e2e-admin@example.com',
  password: 'E2eAdm1n!Str0ng',
}

async function login(page: Page, user: TestUser): Promise<void> {
  // The Vue app mounts only AFTER main.ts's boot hydrate() resolves (it does
  // `session.hydrate().finally(() => app.mount())`). On Vite dev's cold start
  // the on-demand compile makes that slow, and `networkidle` is unreliable
  // (it can settle in a compile gap before mount). Gate on the boot refresh
  // response — mount happens immediately after it — so the submit handler is
  // bound before we click. Otherwise the click hits an un-mounted button, no
  // /api/auth/login fires, and we silently stay on /login. (Prod is unaffected.)
  const bootRefresh = page
    .waitForResponse((r) => r.url().includes('/api/auth/refresh'), { timeout: 30_000 })
    .catch(() => undefined)
  await page.goto('/login')
  await bootRefresh
  await page.getByLabel(/email/i).fill(user.email)
  await page.getByLabel(/password/i).fill(user.password)
  await page.getByRole('button', { name: /log\s*in|sign\s*in|submit/i }).click()
  // NB: a negative-lookahead regex like /(?!.*login).*/ matches ANY url (it
  // succeeds at the end of the string), so it never actually waited. Use a
  // predicate that is true only once we've navigated away from /login.
  await page.waitForURL((url) => !url.pathname.includes('/login'))
}

export const test = base.extend<{ authedPage: Page; adminPage: Page }>({
  authedPage: async ({ page }, use) => {
    await login(page, seedUser)
    await use(page)
  },
  adminPage: async ({ browser }, use) => {
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    await login(page, seedAdmin)
    await use(page)
    await ctx.close()
  },
})

export { expect } from '@playwright/test'
