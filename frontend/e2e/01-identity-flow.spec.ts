import { test, expect } from '@playwright/test'
import { seedUser } from './fixtures/auth'
import { env } from './fixtures/seed'

// Set by the register test; the verify test harvests this address's
// verification mail from MailHog. Serial execution guarantees the order.
let registeredEmail: string | null = null

test.describe('Identity flow: Register → verify → login', () => {
  test('register a new account', async ({ page }) => {
    // Non-reserved domain — the backend's EmailStr 422s reserved TLDs like .test.
    const email = `e2e-${Date.now()}@example.com`
    const password = 'E2eP@ssw0rd!Str0ng'

    await page.goto('/register')
    await page.getByLabel(/email/i).fill(email)
    await page.getByLabel(/password/i).fill(password)
    // The test stack's captcha config is provider=off, so RegisterView renders
    // no captcha widget and the backend skips verification — register submits
    // with email + password only.
    // The submit button reads identity.register.submit = "Create Account"
    // (identity/locales/en.json); it flips to "Creating account..." while in
    // flight, so match either.
    await page.getByRole('button', { name: /creat(e|ing) account/i }).click()
    // RegisterView.vue redirects to /login?pendingVerify=1 on success.
    await page.waitForURL(/\/login\?.*pendingVerify=1/)
    await expect(page).toHaveURL(/pendingVerify=1/)
    registeredEmail = email
  })

  test('verify email via token', async ({ page, request }) => {
    // Token source: an explicit env override wins; otherwise harvest the
    // verification mail the register test above just triggered from MailHog's
    // HTTP API (compose.test.yml maps it to host :8025). Only skip when the
    // mailer is genuinely unreachable — a reachable MailHog with no mail means
    // the SMTP pipeline broke, which must FAIL, not skip.
    let token = env('E2E_VERIFY_TOKEN') ?? null
    if (!token) {
      test.skip(!registeredEmail, 'register test did not run')
      const mailhog = process.env.E2E_MAILHOG_URL ?? 'http://localhost:8025'
      const search = `${mailhog}/api/v2/search?kind=to&query=${encodeURIComponent(registeredEmail!)}`
      let mailhogReachable = false
      const deadline = Date.now() + 20_000
      while (!token && Date.now() < deadline) {
        const res = await request.get(search).catch(() => null)
        if (res?.ok()) {
          mailhogReachable = true
          const data = await res.json()
          for (const item of data.items ?? []) {
            // MailHog stores the raw MIME body: undo quoted-printable soft
            // line breaks and =3D before matching the link (SEC-8 fragment).
            const body = String(item?.Content?.Body ?? '')
              .replace(/=\r?\n/g, '')
              .replace(/=3D/g, '=')
            const m = body.match(/\/verify-email#token=([^\s"'<&]+)/)
            if (m) { token = m[1]!; break }
          }
        }
        if (!token) await new Promise((r) => setTimeout(r, 500))
      }
      test.skip(!mailhogReachable, 'MailHog not reachable (set E2E_MAILHOG_URL)')
      expect(token, `verification email for ${registeredEmail} never arrived in MailHog`).toBeTruthy()
    }
    // Token rides in the URL fragment, not the query string (SEC-8).
    await page.goto(`/verify-email#token=${token}`)
    // identity.verifyEmail.success — the only success copy VerifyEmailView renders.
    await expect(page.getByText('Your email has been verified.')).toBeVisible({ timeout: 10_000 })
  })

  test('login with seeded verified account', async ({ page }) => {
    // Gate on the boot refresh — same pattern as auth.ts. On Vite cold start the
    // app mounts only after hydrate() resolves; clicking before that means no
    // /api/auth/login fires and we silently stay on /login.
    const bootRefresh = page
      .waitForResponse((r) => r.url().includes('/api/auth/refresh'), { timeout: 30_000 })
      .catch(() => undefined)
    await page.goto('/login')
    await bootRefresh
    await page.getByLabel(/email/i).fill(seedUser.email)
    await page.getByLabel(/password/i).fill(seedUser.password)
    await page.getByRole('button', { name: /log\s*in|sign\s*in|submit/i }).click()
    // /(?!.*login).*/ is vacuously true (matches empty string at end of any URL)
    // and resolves before the redirect fires. Use a predicate instead.
    await page.waitForURL((url) => !url.pathname.includes('/login'))
    await expect(page).not.toHaveURL(/login/)
  })
})
