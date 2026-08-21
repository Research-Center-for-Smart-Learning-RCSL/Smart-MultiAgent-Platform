import { test, expect, type Page } from './fixtures/auth'
import { env } from './fixtures/seed'

// Geometry the unit tier cannot reach: jsdom performs no layout, implements
// neither position: sticky nor overflow scrolling, and returns zeros from
// getBoundingClientRect. The structural halves live in the component tests.

/** Box of the shell's scroll container, which owns page scrolling. */
async function contentBox(page: Page) {
  const box = await page.locator('main#main-content').boundingBox()
  expect(box, 'the app shell content region must be present').not.toBeNull()
  return box!
}

/**
 * Give the content region more content than its row can hold.
 *
 * Deliberately synthetic. The claim under test is that the content area owns
 * scrolling *whatever* its content height is, and tying that to how many rows
 * the seed happened to produce is how the defect below hid in the first place:
 * a short list made the shell look correctly sized while its flex base size
 * was still being computed from content.
 */
async function overflowContent(page: Page, height = 3000): Promise<void> {
  await page.evaluate((px) => {
    const filler = document.createElement('div')
    filler.id = 'e2e-overflow-filler'
    filler.style.height = `${px}px`
    document.querySelector('main#main-content')!.append(filler)
  }, height)
  await page.waitForTimeout(150)
}

test.describe('Shell chrome and overlay geometry', () => {
  // T-11 (F-5). The banner was position: fixed at y = 0 over a top bar that
  // also starts at y = 0, covering its upper 33px - most of the wordmark and
  // the top half of the 40x40 sidebar toggle. The pre-existing spec asserted
  // only that the session panel was visible, which says nothing about overlap.
  test('the impersonation banner reserves space instead of covering the top bar', async ({
    adminPage: page,
  }) => {
    test.skip(!env('E2E_TARGET_USER_ID'), 'needs target user')

    await page.goto('/admin/impersonate')
    const form = page.locator('form.admin-impersonate__form')
    await form.getByPlaceholder('Target user UUID').fill(env('E2E_TARGET_USER_ID')!)
    await form.getByRole('button', { name: 'Start Session' }).click()
    const dialog = page.getByRole('alertdialog')
    await expect(dialog).toBeVisible({ timeout: 10_000 })
    await dialog.getByRole('button', { name: 'Start Session' }).click()
    await expect(page.locator('.admin-impersonate__active')).toBeVisible({ timeout: 10_000 })

    const banner = page.locator('.impersonation-banner')
    await expect(banner).toBeVisible()

    const bannerBox = (await banner.boundingBox())!
    const topbarBox = (await page.locator('.app-shell__topbar').boundingBox())!
    expect(bannerBox.y + bannerBox.height).toBeLessThanOrEqual(topbarBox.y + 1)

    // Playwright's actionability check includes a hit-target test, so a toggle
    // still painted over by the banner fails this click rather than passing it
    // through to the element underneath.
    await page.locator('.topbar__sidebar-toggle').click()
  })

  // T-12 (F-8). The wrapper's overflow-x: auto made it the sticky thead's
  // scrollport, and nothing gives it a height, so the header scrolled away
  // with the content region.
  // The shell contract 02-layout-shell.md §3.3 rests on, and the regression
  // this task's own first attempt introduced: `flex: 1` expands to a 0%
  // basis, .app-root carries only min-height so its inner size is indefinite,
  // and a percentage basis against an indefinite container resolves to
  // `content` - the shell grew to 3805px and the document scrolled 3385px.
  // No unit test can see this: jsdom has no layout engine.
  test('the content area owns scrolling, however tall its content is', async ({
    authedPage: page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 420 })
    await page.goto('/orgs')
    await expect(page.locator('main#main-content')).toBeVisible({ timeout: 20_000 })
    await overflowContent(page)

    const geometry = await page.evaluate(() => {
      const main = document.querySelector('main#main-content')!
      const root = document.documentElement
      return {
        mainOverflow: main.scrollHeight - main.clientHeight,
        docOverflow: root.scrollHeight - root.clientHeight,
        shellHeight: document.querySelector('.app-shell')!.clientHeight,
        viewport: window.innerHeight,
      }
    })

    expect(geometry.mainOverflow, 'the content area must be the scroller')
      .toBeGreaterThan(1000)
    expect(geometry.docOverflow, 'the document must not scroll').toBe(0)
    expect(geometry.shellHeight).toBeLessThanOrEqual(geometry.viewport)
  })

  test('a sticky table header stays pinned inside the content area', async ({
    authedPage: page,
  }) => {
    const projectId = env('E2E_PROJECT_ID')
    test.skip(!projectId, 'needs a seeded project')

    await page.setViewportSize({ width: 1440, height: 420 })
    await page.goto(`/projects/${projectId}/agents`)

    const thead = page.locator('main#main-content thead').first()
    await expect(thead).toBeVisible({ timeout: 20_000 })

    // Lengthen the table itself, not the page below it. A sticky header only
    // pins while its own table is still crossing the scrollport, so filler
    // placed after the table would release it before it could be measured -
    // and the seed provides one row, far too short a sticky window.
    await page.evaluate(() => {
      const body = document.querySelector('main#main-content tbody')!
      const row = body.querySelector('tr')
      if (!row) throw new Error('no table row to clone')
      for (let i = 0; i < 40; i += 1) body.append(row.cloneNode(true))
    })
    await page.waitForTimeout(150)

    const main = page.locator('main#main-content')
    const before = (await thead.boundingBox())!
    const box = await contentBox(page)

    // Past the point where the header would otherwise leave the scrollport.
    await main.evaluate((el, headerTop) => {
      el.scrollTop = headerTop + 200
    }, before.y - box.y)
    await expect.poll(async () => main.evaluate((el) => el.scrollTop)).toBeGreaterThan(0)

    const after = (await thead.boundingBox())!
    // Chrome constrains a sticky child against the scrollport's content box,
    // so the header pins one content-gutter below the top bar rather than
    // flush against it. Derived from the computed padding rather than the
    // literal 24px, so the shell's 24/16/8px breakpoint ladder cannot make
    // this red without the contract actually changing.
    const padTop = await main.evaluate((el) =>
      Number.parseFloat(getComputedStyle(el).paddingTop))
    expect(after.y).toBeGreaterThanOrEqual(box.y + padTop - 2)
    expect(after.y).toBeLessThanOrEqual(box.y + padTop + 2)
    expect(after.y).toBeLessThan(before.y)
  })

  // T-13 (F-9). updateMenuPosition read the trigger's rect alone and ran
  // before the menu existed, so a menu opened near the bottom rendered past
  // the viewport with its last items unreachable - the menu is teleported to
  // a body that does not scroll.
  test('a dropdown opened near the viewport bottom keeps every item reachable', async ({
    authedPage: page,
  }) => {
    const projectId = env('E2E_PROJECT_ID')
    test.skip(!projectId, 'needs a seeded project')

    // Deliberately short: this is the condition the flip and the cap exist for.
    await page.setViewportSize({ width: 1366, height: 420 })
    await page.goto(`/projects/${projectId}/agents`)

    // Scoped to the content region: the top bar's own user menu is a
    // .s-dropdown__trigger too, and it is visible before any row renders - an
    // unscoped locator resolves to it and the geometry below means nothing.
    const trigger = page.locator('main#main-content .s-dropdown__trigger').last()
    await expect(trigger).toBeVisible({ timeout: 20_000 })
    await page.locator('main#main-content').evaluate((el) => {
      el.scrollTop = el.scrollHeight
    })
    await page.waitForTimeout(200)

    const triggerBox = (await trigger.boundingBox())!
    const viewport = page.viewportSize()!
    expect(triggerBox.y, 'the trigger must sit low enough to force the flip')
      .toBeGreaterThan(viewport.height / 2)

    await trigger.click()
    const menu = page.locator('.s-dropdown__menu')
    await expect(menu).toBeVisible()

    const items = page.getByRole('menuitem')
    const count = await items.count()
    expect(count).toBeGreaterThan(0)
    for (let i = 0; i < count; i += 1) {
      const box = (await items.nth(i).boundingBox())!
      expect(box.y, `menu item ${i} above the viewport`).toBeGreaterThanOrEqual(-1)
      expect(box.y + box.height, `menu item ${i} below the viewport`)
        .toBeLessThanOrEqual(viewport.height + 1)
    }
  })

  // F-7's other half, and cheap to assert here: an authenticated visitor who
  // mistypes a URL keeps the shell instead of being dropped onto the auth
  // background with no navigation.
  test('an unknown URL keeps the app shell for an authenticated visitor', async ({
    authedPage: page,
  }) => {
    await page.goto('/no-such-section/typo')

    await expect(page.locator('main#main-content')).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.app-shell__topbar')).toBeVisible()
    await expect(page.locator('.auth-layout')).toHaveCount(0)
  })
})
