import { test, expect, type Page } from './fixtures/auth'
import { env } from './fixtures/seed'

/**
 * AC-12 of `docs/tasks/2026-08-22-visual-refinement-phase3-verification-and-debt`
 * - the assertion that would have caught what its visual pass found by looking.
 *
 * Six defects, all at 375px, all older than the typeface work that prompted the
 * pass, and none visible from any unit test: jsdom computes no box, and the
 * computed-style parity baseline compares declarations rather than the space
 * they are given. What they had in common is that a label was allotted a box it
 * could not be read in - the agent list title in 13px, the org detail title in
 * 0px, a key name in 0px - or that content escaped the viewport entirely.
 *
 * The two invariants below are deliberately about *boxes*, not about text.
 * Whether a particular seeded name ellipsises is data, and asserting on it
 * would make this spec fail for reasons that are not defects. That a page does
 * not scroll sideways, and that an element which clips its text still shows
 * some of that text, are properties of the layout and hold whatever the data
 * is.
 *
 * Numbered 25 because 00 must stay first (see its header) and 24 is taken.
 */

const VIEWPORT = { width: 375, height: 812 }

/**
 * The floor a page title's box must clear.
 *
 * Not derived from --page-header-title-min: this asserts the outcome the token
 * was chosen to produce, so reading the token here would make the check agree
 * with itself. 200px is far below the ~343px a title gets on a correct 375px
 * layout and far above the 13px and 0px that were shipping.
 */
const TITLE_FLOOR_PX = 200

/** Below this an element that clips its text is showing none of it. */
const CLIPPED_FLOOR_PX = 40

/**
 * Off-screen-but-readable helpers. Both are clipped to a 1px box on purpose and
 * paint nothing, so neither can be a visual defect.
 */
const HIDDEN_CLASSES = ['sr-only', 'visually-hidden']

interface Probe {
  pageOverflowPx: number
  titles: { text: string; clientW: number }[]
  starved: { tag: string; cls: string; text: string; clientW: number }[]
}

async function probe(page: Page): Promise<Probe> {
  // The floor is passed in rather than restated inside: this callback runs in
  // the page and cannot close over module scope, so a literal here would be a
  // second copy that the constant above no longer controls.
  return page.evaluate(({ hidden, floor }) => {
    const doc = document.documentElement
    const starved: Probe['starved'] = []
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('*'))) {
      const s = getComputedStyle(el)
      if (s.display === 'none' || s.visibility === 'hidden') continue
      if (s.overflowX === 'auto' || s.overflowX === 'scroll') continue
      if (el.children.length > 0) continue
      if (Array.from(el.classList).some((c) => hidden.includes(c))) continue
      const text = (el.textContent ?? '').trim()
      if (!text) continue
      // Only elements that clip: one that wraps is not losing anything.
      const clips =
        s.textOverflow === 'ellipsis' ||
        s.overflowX === 'hidden' ||
        s.overflowX === 'clip' ||
        s.whiteSpace === 'nowrap'
      if (!clips) continue
      if (el.scrollWidth <= el.clientWidth + 1) continue
      if (el.clientWidth >= floor) continue
      starved.push({
        tag: el.tagName.toLowerCase(),
        cls: Array.from(el.classList).join('.'),
        text: text.slice(0, 60),
        clientW: el.clientWidth,
      })
    }
    return {
      pageOverflowPx: doc.scrollWidth - doc.clientWidth,
      titles: Array.from(document.querySelectorAll<HTMLElement>('.s-page-header__title')).map(
        (el) => ({ text: (el.textContent ?? '').trim().slice(0, 40), clientW: el.clientWidth }),
      ),
      starved,
    }
  }, { hidden: HIDDEN_CLASSES, floor: CLIPPED_FLOOR_PX })
}

/** Waits until the element count stops moving; a visible `<main>` is not a settled page. */
async function waitForStableDom(page: Page): Promise<void> {
  let last = -1
  let stable = 0
  for (let i = 0; i < 30 && stable < 3; i++) {
    await page.waitForTimeout(200)
    const n = await page.evaluate(() => document.querySelectorAll('*').length)
    stable = n === last ? stable + 1 : 0
    last = n
  }
}

async function assertSurface(page: Page, id: string, path: string, settle: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator(settle).first()).toBeVisible({ timeout: 30_000 })
  await waitForStableDom(page)

  const r = await probe(page)

  expect(
    r.pageOverflowPx,
    `"${id}" scrolls ${r.pageOverflowPx}px sideways at ${VIEWPORT.width}px`,
  ).toBeLessThanOrEqual(1)

  for (const t of r.titles) {
    expect(
      t.clientW,
      `"${id}" gives its page title "${t.text}" only ${t.clientW}px of box`,
    ).toBeGreaterThanOrEqual(TITLE_FLOOR_PX)
  }

  expect(
    r.starved.map((s) => `${s.tag}.${s.cls} (${s.clientW}px) "${s.text}"`),
    `"${id}" clips text into a box under ${CLIPPED_FLOOR_PX}px, which shows none of it`,
  ).toEqual([])
}

for (const locale of ['en', 'zh-TW'] as const) {
  test.describe(`narrow-viewport layout (${locale})`, () => {
    test.describe.configure({ timeout: 300_000 })

    const projectId = env('E2E_PROJECT_ID')
    const orgId = env('E2E_ORG_ID')
    const agentId = env('E2E_AGENT_ID')
    const chatroomId = env('E2E_CHATROOM_ID')
    const keyGroupId = env('E2E_KEY_GROUP_ID')
    const MAIN = 'main#main-content'

    // One `addInitScript` per test, never a reload. The calls accumulate on the
    // context, and `/api/auth/refresh` rotates its token on every full load -
    // reloading to change locale revokes the session (see fixtures/auth.ts).
    async function useLocale(page: Page): Promise<void> {
      await page.setViewportSize(VIEWPORT)
      await page.addInitScript((l) => {
        try {
          localStorage.setItem('smap-locale', l)
        } catch {
          /* restricted storage */
        }
      }, locale)
    }

    test('authed surfaces keep their labels in a readable box', async ({ authedPage: page }) => {
      test.skip(!projectId || !agentId, 'needs a seeded project and agent')
      await useLocale(page)
      await assertSurface(page, 'agents-list', `/projects/${projectId}/agents`, MAIN)
      if (orgId) await assertSurface(page, 'org-detail', `/orgs/${orgId}`, MAIN)
      if (keyGroupId) {
        await assertSurface(
          page,
          'key-group-detail',
          `/projects/${projectId}/key-groups/${keyGroupId}`,
          MAIN,
        )
      }
      await assertSurface(page, 'agent-tools', `/agents/${agentId}/tools`, MAIN)
      if (chatroomId) await assertSurface(page, 'chatroom', `/chatrooms/${chatroomId}`, MAIN)
    })

    test('the landing page does not scroll sideways', async ({ page }) => {
      await useLocale(page)
      await page.goto('/')
      // The intro curtain covers the page; without dismissing it this measures
      // the curtain and none of Landing.vue.
      await page.keyboard.press('Escape')
      await expect(page.locator('.intro')).toHaveCount(0, { timeout: 15_000 })
      await waitForStableDom(page)
      const r = await probe(page)
      expect(
        r.pageOverflowPx,
        `the landing page scrolls ${r.pageOverflowPx}px sideways at ${VIEWPORT.width}px`,
      ).toBeLessThanOrEqual(1)
    })
  })
}
