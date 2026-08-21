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
  test('a sticky table header stays pinned inside the content area', async ({
    authedPage: page,
  }) => {
    const projectId = env('E2E_PROJECT_ID')
    test.skip(!projectId, 'needs a seeded project')

    // Short enough that the seeded list overflows the content region; the
    // precondition is asserted below rather than assumed.
    await page.setViewportSize({ width: 1440, height: 420 })
    await page.goto(`/projects/${projectId}/agents`)

    const thead = page.locator('main#main-content thead').first()
    await expect(thead).toBeVisible({ timeout: 20_000 })

    const main = page.locator('main#main-content')
    const scrollable = await main.evaluate((el) => el.scrollHeight - el.clientHeight)
    expect(scrollable, 'the content region must overflow for this to mean anything')
      .toBeGreaterThan(40)

    const before = (await thead.boundingBox())!
    await main.evaluate((el) => { el.scrollTop = el.scrollHeight })
    await expect.poll(async () => main.evaluate((el) => el.scrollTop)).toBeGreaterThan(0)

    const after = (await thead.boundingBox())!
    const box = await contentBox(page)
    // Pinned: it did not travel with the scroll, and it is still on screen.
    expect(Math.abs(after.y - before.y)).toBeLessThanOrEqual(2)
    expect(after.y).toBeGreaterThanOrEqual(box.y - 1)
    expect(after.y).toBeLessThan(box.y + box.height)
  })

  // T-13 (F-9). updateMenuPosition read the trigger's rect alone and ran
  // before the menu existed, so a menu opened near the bottom rendered past
  // the viewport with its last items unreachable - the menu is teleported to
  // a body that does not scroll.
  test('a dropdown opened near the viewport bottom keeps every item reachable', async ({
    authedPage: page,
  }) => {
    // Deliberately short: this is the condition the flip and the cap exist for.
    await page.setViewportSize({ width: 1366, height: 420 })
    await page.goto('/keys')

    const trigger = page.locator('.s-dropdown__trigger').last()
    await expect(trigger).toBeVisible({ timeout: 20_000 })

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
