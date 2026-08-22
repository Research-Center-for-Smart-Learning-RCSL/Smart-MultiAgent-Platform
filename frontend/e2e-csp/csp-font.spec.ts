import { test, expect } from '@playwright/test'

/**
 * FU-8 of `docs/tasks/2026-08-22-visual-refinement-phase3-verification-and-debt`,
 * inherited from phase 2.
 *
 * `e2e/24-typography-and-assets.spec.ts` already asserts that no CSP violation
 * was logged and that the font is same-origin — but it runs against Vite on
 * :5173, which sends no CSP header at all, so that assertion cannot fail. The
 * deployed header lives in `deploy/compose/nginx/conf.d/smap.conf` and nothing
 * in this repository ran the frontend behind it. Phase 2 closed the question as
 * "proven by construction"; this spec observes it instead.
 *
 * It runs against a built `dist/` served by nginx with **the CSP extracted from
 * `smap.conf` at job time**, not a copy of it. A copied header would drift and
 * the job would keep passing — which is the same failure in a new costume.
 *
 * The first assertion is on the response header rather than on the font,
 * deliberately: without it every other assertion here passes just as happily
 * against a server that sent no CSP, and the job would be as vacuous as the one
 * it replaces.
 */

interface FontRequest {
  url: string
  status: number
}

test.describe('the deployed CSP admits the self-hosted typeface', () => {
  test('font loads from this origin under smap.conf\'s Content-Security-Policy', async ({
    page,
  }) => {
    const fonts: FontRequest[] = []
    const violations: string[] = []

    page.on('response', (r) => {
      if (/\.woff2?(\?|$)/.test(r.url())) fonts.push({ url: r.url(), status: r.status() })
    })
    page.on('console', (m) => {
      if (/Content Security Policy|Refused to load the font/i.test(m.text())) {
        violations.push(m.text())
      }
    })

    const response = await page.goto('/login')
    expect(response, 'no response for /login').toBeTruthy()

    // The harness is real: this is the header the browser is enforcing below.
    const csp = response!.headers()['content-security-policy']
    expect(csp, 'the document carried no Content-Security-Policy header').toBeTruthy()
    expect(csp, 'the CSP under test has no font-src clause').toContain('font-src')

    await page.locator('form').first().waitFor({ timeout: 30_000 })
    await page.evaluate(() => document.fonts.ready)

    // A blocked font is silent to the user: `font-display: swap` has already
    // painted the fallback, and the page reads perfectly well. `status` is the
    // only thing that separates "loaded" from "gave up and used Arial".
    const loaded = await page.evaluate(
      () =>
        [...document.fonts].filter(
          (f) => f.family.replace(/["']/g, '') === 'Inter' && f.status === 'loaded',
        ).length,
    )
    expect(loaded, 'no Inter face reached status "loaded" under the deployed CSP').toBeGreaterThan(
      0,
    )

    const latin = fonts.filter((f) => f.url.includes('inter-latin.woff2'))
    expect(latin, 'the Latin subset was never fetched').not.toHaveLength(0)
    expect(latin[0].status).toBe(200)

    const origin = new URL(page.url()).origin
    expect(latin[0].url.startsWith(origin), `font served from ${latin[0].url}`).toBe(true)

    expect(violations, 'the deployed CSP refused the font').toEqual([])
  })
})
