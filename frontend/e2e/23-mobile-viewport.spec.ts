import { test, expect, type Page } from './fixtures/auth'
import { env } from './fixtures/seed'

// Viewport boundaries and mobile geometry, in a real browser
// (docs/tasks/2026-08-19-mobile-viewport-and-breakpoints).
//
// This is the only spec the `tablet`, `mobile` and `mobile-xs` projects run —
// see playwright.config.ts for why they are scoped rather than universal. Each
// test below either declares the project whose viewport it needs, or sets its
// own and runs once under `desktop`.
//
// Three of the dossier's findings are NOT here and cannot be: a collapsing URL
// bar (F-45), a virtual keyboard (F-46) and a display cutout (F-25) do not
// exist in headless Chromium at any viewport size. Those are AC-3, AC-4b and
// AC-6, and they are device checks by construction.

const CONTENT = 'main#main-content'

/**
 * Runs this test only under the named Playwright project.
 *
 * A boolean condition, not the callback form: `test.skip(callback, …)` is only
 * legal in a describe block or at file scope, and throws when called from
 * inside a test body — which is where every caller here sits.
 */
function onlyIn(name: string): void {
  test.skip(test.info().project.name !== name, `only meaningful in the ${name} project`)
}

// Both name the selector when the element is missing. This tier does not run
// on a developer host, so a CI failure's message is the whole diagnosis - a
// bare non-null assertion would report only "cannot read properties of null".
async function box(page: Page, selector: string) {
  const b = await page.locator(selector).first().boundingBox()
  expect(b, `${selector} must be present`).not.toBeNull()
  return b!
}

async function lastBox(page: Page, selector: string) {
  const b = await page.locator(selector).last().boundingBox()
  expect(b, `${selector} must be present`).not.toBeNull()
  return b!
}

test.describe('Breakpoint boundaries are exclusive', () => {
  // AC-8 (F-39). The thresholds are MIN-widths, so a mobile-side rule must stop
  // at 479. Seventeen blocks stopped at 480, which handed a viewport at exactly
  // 480 the edge-to-edge phone card while useBreakpoint() reported `sm`. 480 is
  // not an exotic width: it is a DevTools preset, a tablet split-view, and a
  // 1536px screen at 200% zoom.
  //
  // Walked from BOTH sides on purpose. Asserting only that 480 gets the sm
  // treatment would also pass for a rule that stopped at 400 - it proves the
  // treatment applies, not that the boundary sits on the right pixel. 479 is
  // what makes this a boundary test.
  async function authCardTreatment(page: Page, width: number) {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/login')
    await expect(page.locator('.s-auth-card__body')).toBeVisible({ timeout: 20_000 })

    const card = await page.locator('.s-auth-card__body').evaluate((el) => {
      const s = getComputedStyle(el)
      return { radius: s.borderTopLeftRadius, shadow: s.boxShadow }
    })
    const wrapper = await page
      .locator('.auth-layout__wrapper')
      .evaluate((el) => getComputedStyle(el).maxWidth)
    return { ...card, wrapper }
  }

  test('/login flips to the sm card between 479 and 480, not elsewhere', async ({ page }) => {
    onlyIn('desktop')

    // 479: the xs block applies - edge to edge, no radius, no shadow.
    const xs = await authCardTreatment(page, 479)
    expect(xs.radius).toBe('0px')
    expect(xs.shadow).toBe('none')
    expect(xs.wrapper).toBe('none')

    // 480: `sm` starts here, and useBreakpoint() agrees. Before the fix the
    // inclusive `max-width: 480px` gave this width the row above.
    const sm = await authCardTreatment(page, 480)
    expect(sm.radius).not.toBe('0px')
    expect(sm.shadow).not.toBe('none')
    expect(sm.wrapper, '11-responsive-a11y.md:77 puts the 420px card at sm+').toBe('420px')
  })

  // The same defect at the md boundary. ProfileView's mobile block releases the
  // form card's width cap; at exactly 768 the cap must still apply, because
  // useBreakpoint() reports `md` there.
  test('at exactly 768px the form card keeps its md width cap', async ({ authedPage: page }) => {
    onlyIn('tablet')

    await page.goto('/account/profile')
    await expect(page.locator(`${CONTENT} .form-card`).first()).toBeVisible({ timeout: 20_000 })

    const maxWidth = await page
      .locator(`${CONTENT} .form-card`)
      .first()
      .evaluate((el) => getComputedStyle(el).maxWidth)
    expect(maxWidth).toBe('480px')
  })
})

test.describe('The mobile sidebar drawer contains its sidebar', () => {
  async function openDrawer(page: Page) {
    const projectId = env('E2E_PROJECT_ID')
    test.skip(!projectId, 'needs a seeded project')

    await page.goto(`/projects/${projectId}/agents`)
    await expect(page.locator(CONTENT)).toBeVisible({ timeout: 20_000 })
    await page.locator('.topbar__sidebar-toggle').click()
    await expect(page.locator('.s-drawer__panel--sm')).toBeVisible({ timeout: 10_000 })

    // toBeVisible() is true from the first frame of the slide-in, while the
    // panel is still translateX(-100%) and its box is entirely off-screen -
    // measured x = -272 at 320px. Wait for the transform to settle at the left
    // edge before any geometry is read, or every box below is off by a panel
    // width. The transition is --transition-slow (300ms).
    await expect
      .poll(async () => (await page.locator('.s-drawer__panel--sm').boundingBox())?.x, {
        timeout: 5_000,
      })
      .toBe(0)
  }

  // AC-9/AC-10 (F-42). The panel was 320px against a specified min(280px, 85vw)
  // — and the specified width is LESS able to contain the sidebar than the one
  // it replaced, because fitting a 260px sidebar inside the body's 24px gutters
  // needs 308px. Hence the paired `max-width: 100%` on .sidebar: without it the
  // width change alone would turn an overflow that started below 362px into an
  // unconditional one.
  for (const project of ['mobile', 'mobile-xs']) {
    test(`the sidebar does not overflow the drawer (${project})`, async ({ authedPage: page }) => {
      onlyIn(project)
      await openDrawer(page)

      const sidebar = page.locator('.s-drawer__panel--sm .sidebar')
      const overflow = await sidebar.evaluate((el) => ({
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
      }))
      // At 320px this used to be 296 vs 260 — a 36px overflow that clipped the
      // right edge of every nav row and raised a horizontal scrollbar.
      expect(overflow.scrollWidth).toBe(overflow.clientWidth)

      // And every nav row inside the panel's box, not merely the container.
      const panel = await box(page, '.s-drawer__panel--sm')
      const rows = await page.locator('.s-drawer__panel--sm .nav-item').all()
      expect(rows.length, 'the sidebar must have rendered its nav').toBeGreaterThan(0)
      for (const [i, row] of rows.entries()) {
        const r = await row.boundingBox()
        expect(r, `nav row ${i} must be laid out`).not.toBeNull()
        expect(r!.x + r!.width).toBeLessThanOrEqual(panel.x + panel.width + 1)
      }
    })
  }

  test('the drawer honours the specified min(280px, 85vw)', async ({ authedPage: page }) => {
    onlyIn('mobile')
    await openDrawer(page)

    // 375px viewport: min(280, 318.75) = 280.
    const panel = await box(page, '.s-drawer__panel--sm')
    expect(panel.width).toBe(280)
  })

  test('the drawer falls back to 85vw where 280px will not fit', async ({ authedPage: page }) => {
    onlyIn('mobile-xs')
    await openDrawer(page)

    // 320px viewport: min(280, 272) = 272.
    const panel = await box(page, '.s-drawer__panel--sm')
    expect(panel.width).toBeCloseTo(272, 0)
  })
})

test.describe('The agent action bar reserves its own space', () => {
  // AC-12/AC-13 (F-18). The bar was position: fixed, so it added nothing to the
  // scroll height of main.app-shell__content: the scroll range ended before the
  // content the bar covered, and the char count under the prompt editor was
  // unreachable at every scroll position. Sticky reserves exactly its own
  // height, which matters because that height is not constant — create mode
  // renders one button, edit mode two.
  test('the last control clears the bar at the bottom of the scroll', async ({
    authedPage: page,
  }) => {
    onlyIn('mobile')
    const agentId = env('E2E_AGENT_ID')
    test.skip(!agentId, 'needs a seeded agent')

    await page.goto(`/agents/${agentId}?tab=prompt`)
    const bar = page.locator(`${CONTENT} .sticky.bottom-0`)
    await expect(bar).toBeVisible({ timeout: 20_000 })

    await page.locator(CONTENT).evaluate((el) => el.scrollTo(0, el.scrollHeight))
    await page.waitForTimeout(150)

    // The last card of the ACTIVE tab, whose bottom edge the fixed bar used to
    // sit over. `:visible` is load-bearing: the tab panels are `v-show`, so
    // every tab's cards are in the DOM and a bare `.last()` picks one from the
    // Skills panel, which is display:none and has no box at all.
    const cardBox = await lastBox(page, `${CONTENT} form .s-card:visible`)
    const barBox = await box(page, `${CONTENT} .sticky.bottom-0`)

    expect(
      cardBox.y + cardBox.height,
      'the form must end above the bar, not behind it',
    ).toBeLessThanOrEqual(barBox.y + 1)
  })

  test('the bar is reachable at every scroll position', async ({ authedPage: page }) => {
    onlyIn('mobile')
    const agentId = env('E2E_AGENT_ID')
    test.skip(!agentId, 'needs a seeded agent')

    await page.goto(`/agents/${agentId}?tab=prompt`)
    const bar = page.locator(`${CONTENT} .sticky.bottom-0`)
    await expect(bar).toBeVisible({ timeout: 20_000 })

    const content = page.locator(CONTENT)
    const range = await content.evaluate((el) => el.scrollHeight - el.clientHeight)
    expect(range, 'the page must actually scroll for this to mean anything').toBeGreaterThan(0)

    for (const fraction of [0, 0.5, 1]) {
      await content.evaluate((el, f) => el.scrollTo(0, (el.scrollHeight - el.clientHeight) * f), fraction)
      await page.waitForTimeout(100)

      const barBox = await box(page, `${CONTENT} .sticky.bottom-0`)
      const contentBox = await box(page, CONTENT)
      // Sticky pins against the scrollport's CONTENT box, so the bar sits one
      // shell gutter above the bottom edge rather than flush against it. The
      // assertion is that it is inside the scrollport, not that it is flush.
      expect(barBox.y).toBeGreaterThanOrEqual(contentBox.y - 1)
      expect(barBox.y + barBox.height).toBeLessThanOrEqual(contentBox.y + contentBox.height + 1)
    }
  })
})
