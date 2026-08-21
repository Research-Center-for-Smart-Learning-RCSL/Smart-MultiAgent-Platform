import { test, expect, type Page } from './fixtures/auth'
import { env } from './fixtures/seed'

// The content-area padding and scroll contract (02-layout-shell.md §3.3), in a
// real browser. Almost everything this dossier fixes is a rendered geometry:
// jsdom computes no box, applies no scoped CSS, implements neither
// position: sticky nor overflow scrolling. The structural halves - which class
// is on which element, which branch renders - live in the component tests.

const CONTENT = 'main#main-content'

/** The shell's padding at the current viewport, read rather than assumed. */
async function contentPadding(page: Page) {
  return page.locator(CONTENT).evaluate((el) => {
    const s = getComputedStyle(el)
    return {
      top: Number.parseFloat(s.paddingTop),
      left: Number.parseFloat(s.paddingLeft),
      bottom: Number.parseFloat(s.paddingBottom),
    }
  })
}

async function box(page: Page, selector: string) {
  const b = await page.locator(selector).first().boundingBox()
  expect(b, `${selector} must be present`).not.toBeNull()
  return b!
}

test.describe('Content area padding', () => {
  // T-10 (F-3). 34 view roots added a second padding layer on top of the
  // shell's, so the documented 24px gutter rendered as 48px on 26 routes and
  // 40px on 7 more, and 32px on a small phone where §3.3 specifies 8px.
  // Asserted against the shell's own computed padding rather than against the
  // literal numbers, so the breakpoint ladder can move without this going red
  // for the wrong reason; the ladder's values are pinned separately below.
  for (const vp of [
    { width: 1440, height: 900, padding: 24 },
    { width: 900, height: 800, padding: 16 },
    { width: 375, height: 812, padding: 8 },
  ]) {
    test(`the page header sits one shell gutter in at ${vp.width}x${vp.height}`, async ({
      authedPage: page,
    }) => {
      const projectId = env('E2E_PROJECT_ID')
      test.skip(!projectId, 'needs a seeded project')

      await page.setViewportSize({ width: vp.width, height: vp.height })
      await page.goto(`/projects/${projectId}/agents`)
      await expect(page.locator(`${CONTENT} .s-page-header`)).toBeVisible({ timeout: 20_000 })

      const padding = await contentPadding(page)
      expect(padding.left, 'the shell padding ladder (AC-17)').toBe(vp.padding)

      const main = await box(page, CONTENT)
      const header = await box(page, `${CONTENT} .s-page-header`)

      // One gutter, not two. Sub-pixel tolerance only.
      expect(header.x - main.x).toBeGreaterThanOrEqual(padding.left - 1)
      expect(header.x - main.x).toBeLessThanOrEqual(padding.left + 1)
      expect(header.y - main.y).toBeGreaterThanOrEqual(padding.top - 1)
      expect(header.y - main.y).toBeLessThanOrEqual(padding.top + 1)
    })
  }

  // T-16 (F-40). The shell renders the landmark; 23 view roots nested a second
  // one inside it, so the skip link landed on a wrapper whose only child was
  // another main.
  test('exactly one main landmark exists on an authenticated route', async ({
    authedPage: page,
  }) => {
    const projectId = env('E2E_PROJECT_ID')
    test.skip(!projectId, 'needs a seeded project')

    await page.goto(`/projects/${projectId}/agents`)
    await expect(page.locator(CONTENT)).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('main')).toHaveCount(1)
  })
})

/**
 * Make the content area scrollable and leave it scrolled.
 *
 * Synthetic on purpose: the claim under test is that the offset is reset
 * whatever made the page long, and tying that to how many rows a seed happened
 * to produce is how a green run comes to mean nothing.
 */
async function scrollAway(page: Page): Promise<number> {
  await page.locator(CONTENT).evaluate((el) => {
    const filler = document.createElement('div')
    filler.className = 'e2e-scroll-filler'
    filler.style.height = '3000px'
    el.append(filler)
    el.scrollTop = 1200
  })
  await page.waitForTimeout(150)
  return page.locator(CONTENT).evaluate((el) => el.scrollTop)
}

test.describe('Scroll position on navigation', () => {
  // T-11 (F-4). AppShell outlives every authenticated route and App.vue passes
  // no key, so main and its scrollTop survived the view swap: navigating off a
  // long table landed mid-page on the next view with its header above the fold.
  // Both halves must be client-side navigations - page.goto() is a document
  // load, which resets the offset for reasons that have nothing to do with the
  // fix and would make this pass against the unfixed code.
  test('a path change lands at the top of the next view', async ({ authedPage: page }) => {
    await page.setViewportSize({ width: 1440, height: 700 })
    await page.goto('/orgs')
    await expect(page.locator(CONTENT)).toBeVisible({ timeout: 20_000 })

    expect(await scrollAway(page), 'the content area must be scrollable here')
      .toBeGreaterThan(0)

    await page.locator('.app-shell__sidebar a[href="/keys"]').first().click()
    await expect(page).toHaveURL(/\/keys$/, { timeout: 20_000 })
    await page.waitForTimeout(200)

    expect(await page.locator(CONTENT).evaluate((el) => el.scrollTop)).toBe(0)
  })

  // The guard against over-applying the reset: the watcher keys on path, so a
  // tab switch - which is a query-only router.replace - keeps the reader's
  // place. This half passes today and must keep passing.
  test('a query-only change preserves the offset', async ({ authedPage: page }) => {
    const agentId = env('E2E_AGENT_ID')
    test.skip(!agentId, 'needs a seeded agent')

    await page.setViewportSize({ width: 1440, height: 700 })
    await page.goto(`/agents/${agentId}`)
    await expect(page.locator(`${CONTENT} .s-page-header`)).toBeVisible({ timeout: 20_000 })

    const offset = await scrollAway(page)
    expect(offset).toBeGreaterThan(0)

    // Agent detail's tab bar writes { ...query, tab } through router.replace,
    // leaving the path untouched.
    await page.getByRole('tab', { name: 'Knowledge', exact: true }).click()
    await expect(page).toHaveURL(/[?&]tab=knowledge/, { timeout: 10_000 })
    await page.waitForTimeout(200)

    expect(await page.locator(CONTENT).evaluate((el) => el.scrollTop)).toBe(offset)
  })
})

test.describe('Views that size themselves against the shell', () => {
  // T-12 (F-10). The graph root recomputed 100vh - 3.5rem, which counts the
  // topbar but not the 24/16/8px the shell adds top and bottom, so it declared
  // 48px more height than the content box has: a fixed-height canvas page with
  // 48px of page scroll and a topbar shadow that should never appear.
  test('the concept map graph leaves the content area with no scroll travel', async ({
    authedPage: page,
  }) => {
    const projectId = env('E2E_PROJECT_ID')
    test.skip(!projectId, 'needs a seeded project')

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`/projects/${projectId}/graphrag-configs`)

    // The graph is reachable only through a row's action menu, and only when
    // the seed produced a config. Skip rather than assert the seed always did.
    const trigger = page.locator(`${CONTENT} table tbody .s-dropdown__trigger`).first()
    let hasConfig = true
    try {
      await trigger.waitFor({ state: 'visible', timeout: 15_000 })
    } catch {
      hasConfig = false
    }
    test.skip(!hasConfig, 'no concept map config to open a graph for')

    await trigger.click()
    await page.getByRole('menuitem', { name: 'View Graph', exact: true }).click()
    await expect(page).toHaveURL(/\/graph$/, { timeout: 20_000 })
    await expect(page.locator(`${CONTENT} .s-page-header`)).toBeVisible({ timeout: 20_000 })
    await page.waitForTimeout(300)

    const travel = await page.locator(CONTENT).evaluate((el) => el.scrollHeight - el.clientHeight)
    expect(travel, 'a fixed-height canvas page must not scroll').toBeLessThanOrEqual(1)
    await expect(page.locator('.app-shell__topbar--scrolled')).toHaveCount(0)
  })

  // T-14 (F-16). Below 1024px the panel's grid cell carried only a min-height,
  // so its h-full resolved against an indefinite height and it grew with its
  // own conversation until the composer left the page entirely.
  test('the prompt assistant stays bounded below the lg breakpoint', async ({
    authedPage: page,
  }) => {
    const agentId = env('E2E_AGENT_ID')
    test.skip(!agentId, 'needs a seeded agent')

    await page.setViewportSize({ width: 900, height: 1200 })
    await page.goto(`/agents/${agentId}?tab=prompt`)

    const panel = page.locator(`${CONTENT} .lg\\:sticky`).first()
    let hasPanel = true
    try {
      await panel.waitFor({ state: 'visible', timeout: 15_000 })
    } catch {
      hasPanel = false
    }
    test.skip(!hasPanel, 'the prompt assistant panel did not render')

    // Synthetic content, for the same reason as the scroll filler: the bound
    // must hold whatever the conversation length, and no e2e run can produce
    // real assistant turns without a provider key.
    await panel.evaluate((el) => {
      const filler = document.createElement('div')
      filler.style.height = '3000px'
      el.firstElementChild!.append(filler)
    })
    await page.waitForTimeout(150)

    const rect = (await panel.boundingBox())!
    // h-[32rem]; below lg it used to be a min-height and grew with its content.
    expect(rect.height).toBeGreaterThanOrEqual(510)
    expect(rect.height).toBeLessThanOrEqual(514)
  })

  // T-15 (F-51). The sticky constant counted the shell's padding once (8rem)
  // where the geometry needs the topbar plus a symmetric 24px pair, leaving
  // 24px of dead band below the panel relative to the content box.
  test('the sticky prompt assistant ends at the content-box bottom', async ({
    authedPage: page,
  }) => {
    const agentId = env('E2E_AGENT_ID')
    test.skip(!agentId, 'needs a seeded agent')

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`/agents/${agentId}?tab=prompt`)

    const panel = page.locator(`${CONTENT} .lg\\:sticky`).first()
    let hasPanel = true
    try {
      await panel.waitFor({ state: 'visible', timeout: 15_000 })
    } catch {
      hasPanel = false
    }
    test.skip(!hasPanel, 'the prompt assistant panel did not render')

    const topbar = await box(page, '.app-shell__topbar')
    const padding = await contentPadding(page)
    const viewport = page.viewportSize()!

    const rect = (await panel.boundingBox())!
    // viewport - topbar - the 24px it sticks at - the 24px the shell reserves
    // below, which is exactly what lg:h-[calc(100vh-3.5rem-3rem)] expresses.
    const expected = viewport.height - topbar.height - padding.top - padding.bottom
    expect(rect.height).toBeGreaterThanOrEqual(expected - 2)
    expect(rect.height).toBeLessThanOrEqual(expected + 2)
  })
})

test.describe('Loading states', () => {
  // T-13 (F-27). A skeleton taller than the state its branch settles to pulls
  // the page up under the cursor when the query lands. /invites rendered 384px
  // of skeleton against a ~176px empty state; /account/sessions rendered 264px
  // against an empty state or a single ~60px row.
  for (const view of [
    { path: '/invites', api: '**/api/invites/inbox*', body: '[]' },
    { path: '/account/sessions', api: '**/api/auth/sessions*', body: '[]' },
  ]) {
    test(`the skeleton on ${view.path} is no taller than its settled state`, async ({
      authedPage: page,
    }) => {
      await page.route(view.api, async (route) => {
        await new Promise((r) => setTimeout(r, 1500))
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: view.body,
        })
      })

      await page.setViewportSize({ width: 1440, height: 900 })
      await page.goto(view.path)

      const skeleton = page.locator('.s-skeleton').first()
      await expect(skeleton).toBeVisible({ timeout: 10_000 })
      const whileLoading = await page.locator(CONTENT)
        .evaluate((el) => el.scrollHeight)

      await expect(page.locator('.s-skeleton')).toHaveCount(0, { timeout: 15_000 })
      await page.waitForTimeout(200)
      const settled = await page.locator(CONTENT).evaluate((el) => el.scrollHeight)

      expect(settled, 'the page must not collapse upward when the query lands')
        .toBeGreaterThanOrEqual(whileLoading)
    })
  }
})
