import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test'

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

// Log in per test, each in its own fresh context. Two alternatives were ruled out
// by observation:
//   * Storage-state reuse — /api/auth/refresh rotates the refresh token and treats
//     a reused one as an attack (revoking the family), so a saved cookie only
//     authenticates the first context and 401s the rest.
//   * A shared worker context — after several navigations its refresh chain hits a
//     rotation race, the session is revoked, and every later test in the worker
//     bounces to /login. A fresh per-test session keeps each test's single boot
//     refresh clean.
//
// The fixture handles the two real login-flakiness sources:
//   1. Fill-before-hydrate race. The Vue app mounts only AFTER main.ts's boot
//      hydrate() resolves (`session.hydrate().finally(() => app.mount())`); on Vite
//      dev's cold start that is slow, so a fill()+click() landing before mount hits
//      an un-bound `@submit` handler — `submit()` never runs, no /api/auth/login
//      fires, and we silently stay on /login. We gate on hydration (the submit
//      button is enabled only once the form is interactive) and treat the login
//      *response* as ground truth: absent it, the click raced the mount, so re-drive.
//   2. Rate limiting. The backend AUTH bucket allows only 10 req/min/IP and every
//      boot re-authenticates via /api/auth/refresh, so a busy suite brushes the
//      limit; on a 429 we back off on Retry-After and retry rather than time out on
//      /login. (Valid logins never trip the failed-login lockout, so re-driving is
//      safe.)
async function login(page: Page, user: TestUser): Promise<void> {
  const emailInput = page.getByLabel(/email/i)
  const passwordInput = page.getByLabel(/password/i)
  const submitBtn = page.getByRole('button', { name: /log\s*in|sign\s*in|submit/i })

  for (let attempt = 1; attempt <= 6; attempt++) {
    await page.goto('/login')
    await emailInput.waitFor({ state: 'visible', timeout: 30_000 })
    await expect(submitBtn).toBeEnabled({ timeout: 30_000 })

    await emailInput.fill(user.email)
    await passwordInput.fill(user.password)

    const loginResp = page
      .waitForResponse((r) => r.url().includes('/api/auth/login'), { timeout: 15_000 })
      .catch(() => null)
    await submitBtn.click()
    const resp = await loginResp

    if (!resp) {
      await page.waitForTimeout(500) // click raced the mount — re-drive
      continue
    }
    if (resp.status() === 429) {
      // AUTH bucket exhausted (10/min/IP); respect Retry-After (capped) and retry.
      const retryAfter = Number(resp.headers()['retry-after'] ?? '10')
      await page.waitForTimeout(Math.min(Math.max(retryAfter, 1), 60) * 1000 + 500)
      continue
    }
    if (!resp.ok()) {
      throw new Error(`login POST failed with status ${resp.status()} for ${user.email}`)
    }
    // Predicate, not a negative-lookahead regex — that matches any URL at
    // end-of-string and so never actually waits.
    await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 20_000 })
    return
  }
  throw new Error(`login for ${user.email} did not succeed after 6 attempts (mount race or lockout)`)
}

/**
 * A `Bearer` header for `user`, for setup calls a spec makes through
 * Playwright's own `request` context.
 *
 * `page.request` is NOT a substitute, however much it looks like one: only the
 * refresh token is a cookie, while the access token lives in the SPA's memory
 * and rides in `Authorization` (`src/shared/transport/axios.ts`). A call made
 * through the page's cookie jar therefore reaches the API unauthenticated and
 * 401s.
 *
 * Logging in on the page's own context is not a substitute either — that
 * rotates the refresh cookie the live UI session is using, and the backend
 * treats a reused refresh token as an attack and revokes the family. Hence a
 * separate context: same user, same authorization, its own jar.
 */
export async function bearerFor(
  api: APIRequestContext,
  user: TestUser = seedUser,
): Promise<Record<string, string>> {
  const resp = await api.post('/api/auth/login', { data: user })
  if (!resp.ok()) {
    throw new Error(`API login failed with status ${resp.status()} for ${user.email}`)
  }
  const { access_token: token } = (await resp.json()) as { access_token: string }
  return { Authorization: `Bearer ${token}` }
}

export const test = base.extend<{ authedPage: Page; adminPage: Page }>({
  authedPage: async ({ page }, use) => {
    await login(page, seedUser)
    await use(page)
  },
  adminPage: async ({ browser }, use) => {
    const ctx = await browser.newContext()
    try {
      const page = await ctx.newPage()
      await login(page, seedAdmin)
      await use(page)
    } finally {
      // Ensures the context is closed even if login() throws (e.g. exhausts
      // its retry loop) — otherwise it leaks for the rest of the worker
      // process's lifetime, since Playwright only owns/tears down the
      // built-in `page` fixture, not a context this fixture created itself.
      await ctx.close()
    }
  },
})

export { expect } from '@playwright/test'
