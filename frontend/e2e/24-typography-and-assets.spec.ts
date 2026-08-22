import { test, expect } from './fixtures/auth'

/**
 * AC-1, AC-2, AC-7 and AC-8 of
 * `docs/tasks/2026-08-21-visual-refinement-phase2-identity-and-depth`.
 *
 * A webfont fails quietly. If the CSP blocks it, if the `unicode-range` is
 * wrong, if the preload is discarded and the file fetched twice, or if a CJK
 * screen drags down 83 KB it will never draw a glyph from — every one of those
 * renders a perfectly readable page in the fallback stack. Nothing in the unit
 * tier can see any of it: jsdom loads no fonts, applies no CSP and has no
 * network panel.
 *
 * Numbered 24 deliberately. 00 is the visual-parity baseline and must stay the
 * first spec to run (its own header explains why); this one mutates nothing, but
 * 20 through 23 are taken and reusing a number would silently displace a spec.
 */

/** Requests this spec cares about, recorded per test. */
type FontRequest = { url: string; status: number }

function watchFonts(page: import('@playwright/test').Page): {
  fonts: FontRequest[]
  offOrigin: string[]
  cspViolations: string[]
} {
  const fonts: FontRequest[] = []
  const offOrigin: string[] = []
  const cspViolations: string[] = []

  page.on('response', (r) => {
    const url = r.url()
    if (/\.woff2?(\?|$)/.test(url)) fonts.push({ url, status: r.status() })
  })

  page.on('request', (r) => {
    const url = r.url()
    // Any third-party font host at all, however it were reached.
    if (/fonts\.(googleapis|gstatic)\.com|use\.typekit|fonts\.bunny\.net/.test(url)) {
      offOrigin.push(url)
    }
  })

  // A blocked font is silent to the user and shows up only here.
  page.on('console', (m) => {
    if (/Content Security Policy|Refused to load the font/i.test(m.text())) {
      cspViolations.push(m.text())
    }
  })

  return { fonts, offOrigin, cspViolations }
}

test.describe('interface typeface', () => {
  test('resolves to Inter, from this origin, fetched once', async ({ page }) => {
    const watch = watchFonts(page)

    await page.goto('/login')
    // Not networkidle: the app holds live sockets and never idles.
    // document.fonts.ready is the real signal anyway — the face has to be
    // usable, not merely requested, and font-display: swap paints in the
    // fallback first, so asserting on the stack alone proves nothing.
    await page.locator('form').first().waitFor()
    await page.evaluate(() => document.fonts.ready)

    const stack = await page.evaluate(() => getComputedStyle(document.body).fontFamily)
    expect(stack).toMatch(/^["']?Inter["']?\s*,/)

    const loaded = await page.evaluate(() =>
      [...document.fonts].filter((f) => f.family.replace(/["']/g, '') === 'Inter' && f.status === 'loaded').length,
    )
    expect(loaded, 'no Inter face reached status "loaded"').toBeGreaterThan(0)

    // AC-1: exactly one request for the Latin subset, and it succeeded.
    const latin = watch.fonts.filter((f) => f.url.includes('inter-latin.woff2'))
    expect(latin).toHaveLength(1)
    expect(latin[0].status).toBe(200)

    // Same origin as the document. This is what makes the CSP question moot.
    const origin = new URL(page.url()).origin
    expect(latin[0].url.startsWith(origin), `font served from ${latin[0].url}`).toBe(true)

    // AC-1 and AC-7.
    expect(watch.offOrigin, 'a third-party font host was contacted').toEqual([])
    expect(watch.cspViolations, 'the font was refused by the CSP').toEqual([])
  })

  test('preloads the Latin subset from the document head', async ({ page }) => {
    await page.goto('/login')
    const preload = page.locator('link[rel="preload"][as="font"]')
    await expect(preload).toHaveCount(1)
    await expect(preload).toHaveAttribute('href', '/fonts/inter-latin.woff2')
    // Required even same-origin: a font is always fetched in CORS mode, and
    // without it the preload is discarded and the file fetched a second time.
    await expect(preload).toHaveAttribute('crossorigin', /.*/)
  })

  /**
   * AC-2. Inter has no CJK coverage. Without the `unicode-range` a browser may
   * fetch a file only to discover that, and the Latin-Extended subset is 83 KB
   * an en/zh-TW interface has no use for.
   */
  test('does not fetch Latin-Extended for a screen that has no Latin-Extended', async ({ page }) => {
    const watch = watchFonts(page)

    await page.goto('/login')
    await page.locator('form').first().waitFor()
    await page.evaluate(() => document.fonts.ready)

    expect(watch.fonts.filter((f) => f.url.includes('inter-latin-ext'))).toEqual([])
  })

  test('renders CJK from the CJK stack, not through Inter', async ({ page }) => {
    await page.goto('/login')
    await page.locator('form').first().waitFor()
    await page.evaluate(() => document.fonts.ready)

    // Ask the renderer which face it actually used, via CDP. document.fonts
    // .check() is the obvious API and the wrong one: it reports whether every
    // face matching the family is loaded, so it answers false for Inter purely
    // because the Latin-Extended subset has (correctly) never been fetched.
    // getPlatformFontsForNode reports what was really put on the glyphs.
    await page.evaluate(() => {
      const make = (id: string, text: string) => {
        const el = document.createElement('p')
        el.id = id
        el.textContent = text
        el.style.fontFamily = 'var(--font-body)'
        el.style.fontSize = '16px'
        document.body.appendChild(el)
      }
      make('probe-latin', 'Sign in to your account')
      make('probe-cjk', '登入帳號與密碼')
    })

    const cdp = await page.context().newCDPSession(page)
    await cdp.send('DOM.enable')
    await cdp.send('CSS.enable')
    const { root } = await cdp.send('DOM.getDocument')

    async function facesFor(selector: string): Promise<string[]> {
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector })
      const { fonts } = await cdp.send('CSS.getPlatformFontsForNode', { nodeId })
      return fonts.map((f) => f.familyName)
    }

    const latinFaces = await facesFor('#probe-latin')
    const cjkFaces = await facesFor('#probe-cjk')

    expect(latinFaces.join(','), 'Latin did not render in Inter').toContain('Inter')
    expect(cjkFaces.join(','), 'CJK resolved through Inter, so the unicode-range is not scoped')
      .not.toContain('Inter')
  })

  /**
   * AC-8. The gate script iterates `dist/assets/*.js` only, so it structurally
   * cannot see a font — the number is asserted here instead of being assumed
   * safe because a check that cannot fail reported nothing.
   */
  test('keeps the critical-path font under its budget', async ({ page }) => {
    const response = await page.request.get('/fonts/inter-latin.woff2')
    expect(response.status()).toBe(200)
    expect(response.headers()['content-type']).toMatch(/font\/woff2|application\/font-woff2|application\/octet-stream/)

    const bytes = (await response.body()).length
    expect(bytes, `inter-latin.woff2 is ${bytes} B`).toBeLessThan(120 * 1024)
  })
})

test.describe('surface depth', () => {
  /**
   * AC-6's measurable half. Whether a card "reads as layered" is a judgement,
   * but whether it is a different colour from the page behind it is not, and
   * that is the part that was false: both were --color-bg, so no shadow value
   * could have helped.
   */
  for (const theme of ['light', 'dark'] as const) {
    test(`separates the canvas from a raised sheet in the ${theme} theme`, async ({ page }) => {
      await page.goto('/login')
      await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme)

      const { canvas, sheet, gap } = await page.evaluate(() => {
        const parse = (v: string): [number, number, number] => {
          const m = v.match(/\d+(\.\d+)?/g) ?? []
          return [Number(m[0]), Number(m[1]), Number(m[2])]
        }
        // Resolve through a real element so the browser normalises the value.
        const probe = document.createElement('div')
        document.body.appendChild(probe)
        const resolve = (token: string): [number, number, number] => {
          probe.style.color = `var(${token})`
          const rgb = parse(getComputedStyle(probe).color)
          return rgb
        }
        const lstar = ([r, g, b]: [number, number, number]): number => {
          const lin = (c: number) => {
            const v = c / 255
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
          }
          const y = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
          return y <= 216 / 24389 ? y * (24389 / 27) : 116 * Math.cbrt(y) - 16
        }
        const canvasRgb = resolve('--color-canvas')
        const sheetRgb = resolve('--color-bg')
        probe.remove()
        return {
          canvas: canvasRgb,
          sheet: sheetRgb,
          gap: Math.abs(lstar(canvasRgb) - lstar(sheetRgb)),
        }
      }, theme)

      expect(canvas, '--color-canvas did not resolve').not.toEqual([0, 0, 0])
      expect(sheet, '--color-bg did not resolve').not.toEqual([0, 0, 0])
      expect(canvas, 'the canvas and the sheet are the same colour').not.toEqual(sheet)
      expect(gap, `canvas-to-sheet gap is ${gap.toFixed(2)} L*`).toBeGreaterThanOrEqual(3)
      expect(gap, `canvas-to-sheet gap is ${gap.toFixed(2)} L*`).toBeLessThanOrEqual(5)
    })
  }
})

test.describe('interaction feedback', () => {
  /**
   * AC-4's browser half. The unit tier asserts the :active rules exist; only a
   * browser can say the rule actually applies to a held-down button.
   */
  test('depresses a button while it is held', async ({ page }) => {
    await page.goto('/login')
    const button = page.getByRole('button', { name: /sign in|log ?in|登入/i }).first()
    await expect(button).toBeVisible()

    const box = await button.boundingBox()
    expect(box).not.toBeNull()

    const resting = await button.evaluate((el) => getComputedStyle(el).transform)

    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
    await page.mouse.down()
    // The press transitions over --duration-fast; sample after it settles.
    await page.waitForTimeout(250)
    const pressed = await button.evaluate((el) => getComputedStyle(el).transform)
    await page.mouse.up()

    expect(pressed, 'the button looks identical while held down').not.toBe(resting)
    // 1px down, per the motion language.
    expect(pressed).toMatch(/matrix\(1, 0, 0, 1, 0, 1\)/)
  })

  /**
   * AC-5's mechanical half: that a focused control has an outline at all. Which
   * backdrop it sits on, and whether it reads correctly there, stays a manual
   * judgement recorded in the dossier's §15.
   */
  test('shows a ring on every control a keyboard traversal reaches', async ({ page }) => {
    await page.goto('/login')
    // Wait for hydration before driving the keyboard: Tab against an un-mounted
    // app moves focus nowhere and the assertion fails for the wrong reason.
    const email = page.locator('input[type="email"]').first()
    await email.waitFor()
    await email.focus()

    /**
     * The ring is not always on the focused element, and that is by design:
     * SInput's field carries `outline: none` because the bordered wrapper draws
     * the ring on `:focus-within`, and a clipped checkbox hands it to a visible
     * sibling. So the question AC-5 actually asks is "can the user see where
     * focus is", which means looking at the element *or* what encloses it.
     */
    const probe = async (): Promise<{ path: string; ringOn: string | null }> => (
      page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null
        if (!el || el === document.body) return { path: '(none)', ringOn: null }

        const describe = (n: Element) =>
          `${n.tagName.toLowerCase()}${n.className ? `.${String(n.className).split(/\s+/)[0]}` : ''}`

        let node: Element | null = el
        for (let depth = 0; node && depth < 4; depth++, node = node.parentElement) {
          const s = getComputedStyle(node)
          if (s.outlineStyle !== 'none' && Number.parseFloat(s.outlineWidth) >= 2) {
            return { path: describe(el), ringOn: describe(node) }
          }
        }
        return { path: describe(el), ringOn: null }
      })
    )

    const unringed: string[] = []
    let reached = 0
    // Enough stops to cross the form's fields, its submit button and the
    // Google button below it.
    for (let i = 0; i < 8; i++) {
      const { path, ringOn } = await probe()
      if (path !== '(none)') {
        reached++
        if (ringOn === null) unringed.push(path)
      }
      await page.keyboard.press('Tab')
    }

    expect(reached, 'the traversal reached no controls at all').toBeGreaterThan(3)
    expect(unringed, 'these controls take focus with no visible ring').toEqual([])
  })
})
