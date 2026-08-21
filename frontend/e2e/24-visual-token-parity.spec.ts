import { test, expect, type Page } from './fixtures/auth'
import { env } from './fixtures/seed'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import { dirname, resolve } from 'path'
import { fileURLToPath } from 'url'

/**
 * C-0 of `docs/tasks/2026-08-21-visual-refinement-phase1-token-adoption`.
 *
 * The dossier replaces ~940 literal type/spacing declarations with `var(--...)`
 * references whose declared values are identical, and its acceptance bar (AC-1)
 * is that no rendered pixel moves. Nothing in the unit tier can see that: jsdom
 * computes no box and applies no scoped CSS. This spec is the only thing in the
 * repository that compares a rendered box before and after.
 *
 * It does not name selectors. A hand-written selector list only protects what
 * the author remembered, and the failure this exists to catch - a substitution
 * that is individually correct but lands in the wrong rule - is by definition
 * somewhere the author was not looking. Instead every element carrying a class
 * is keyed by `tag|sorted-class-list` and the *set* of distinct computed-style
 * records seen under that key is recorded, so the baseline covers whatever the
 * page actually renders. That key is stable under this refactor by
 * construction: the dossier changes no markup, no props and no class names
 * (its Section 3).
 *
 * A set rather than the first occurrence, because a utility class is not a
 * component: `span.sr-only` appears inside a button at 12px/600 and beside one
 * at 16px/400, and which of the two comes first in DOM order moves with the
 * data. Recording both makes the key order-independent.
 *
 *   pnpm exec playwright test 24-visual-token-parity --project=desktop
 *     compares against e2e/baselines/visual-token-parity.json
 *
 *   UPDATE_VISUAL_BASELINE=1 pnpm exec playwright test 24-visual-token-parity --project=desktop
 *     rewrites it. Only legitimate against unmodified code, or when the visual
 *     language is deliberately changed (phase 2).
 */

const HERE = dirname(fileURLToPath(import.meta.url))
const BASELINE_DIR = resolve(HERE, 'baselines')
const BASELINE_FILE = resolve(BASELINE_DIR, 'visual-token-parity.json')
const UPDATE = process.env.UPDATE_VISUAL_BASELINE === '1'

/**
 * The Q-2 property set, plus the two properties that carry the token families
 * this dossier adds: `min-height` for the control-height ladder and
 * `box-shadow` for semantic elevation.
 *
 * Measured `height`/`width` are deliberately absent - they vary with text and
 * with seeded data, so they cannot serve a byte-equality baseline. The ladder
 * is *declared* as `min-height` in every one of the five files AC-5 names, so
 * comparing `min-height` pins it exactly.
 */
const PROPS = [
  'font-size',
  'font-weight',
  'line-height',
  'padding-top',
  'padding-right',
  'padding-bottom',
  'padding-left',
  'margin-top',
  'margin-right',
  'margin-bottom',
  'margin-left',
  'row-gap',
  'column-gap',
  'min-height',
  'box-shadow',
] as const

/**
 * Values omitted from the record, restored on comparison. Roughly 40% of the
 * declarations in the codebase resolve to `0px`, and writing them all out
 * quadruples the baseline for no signal.
 */
const OMIT: Record<string, string> = {
  'padding-top': '0px',
  'padding-right': '0px',
  'padding-bottom': '0px',
  'padding-left': '0px',
  'margin-top': '0px',
  'margin-right': '0px',
  'margin-bottom': '0px',
  'margin-left': '0px',
  'row-gap': 'normal',
  'column-gap': 'normal',
  'min-height': '0px',
  'box-shadow': 'none',
}

/**
 * Properties whose used value can be derived from the box rather than declared:
 * `margin: auto` centres against the content width, and a percentage
 * `min-height` resolves against the parent. Every literal this dossier replaces
 * is a whole number of pixels, so a fractional value under one of these can
 * only be a measured one - it is recorded as a sentinel instead, which keeps a
 * text-width change from reading as a token regression.
 */
const MEASURED_IF_FRACTIONAL = ['margin-top', 'margin-right', 'margin-bottom', 'margin-left', 'min-height']
const MEASURED = '<measured>'

/** Unclassed elements worth keying anyway - their styling comes from @layer base. */
const SEMANTIC_TAGS = ['H1', 'H2', 'H3', 'H4', 'TH', 'TD', 'LEGEND', 'FIELDSET', 'INPUT', 'SELECT', 'TEXTAREA']

/** Signature -> the distinct property records observed under it, sorted. */
type Snapshot = Record<string, string[]>
type Baseline = Record<string, Snapshot>

/**
 * Reads every classed element's computed style. Runs in the page, so it takes
 * its configuration as an argument rather than closing over module scope.
 *
 * Each record is serialised in `PROPS` order as a single `|`-joined string:
 * deduplication needs a comparable value, and the flat form also roughly halves
 * the committed baseline against one JSON object per element.
 */
async function capture(page: Page): Promise<Snapshot> {
  return page.evaluate(
    ({ props, omit, semanticTags, measuredProps, measured }) => {
      const seen: Record<string, Set<string>> = {}
      for (const el of Array.from(document.querySelectorAll<HTMLElement>('*'))) {
        const classes = Array.from(el.classList).sort()
        if (classes.length === 0 && !semanticTags.includes(el.tagName)) continue
        const key = `${el.tagName.toLowerCase()}|${classes.join('.')}`
        const s = getComputedStyle(el)
        const parts: string[] = []
        for (const p of props) {
          let v = s.getPropertyValue(p).trim()
          if (measuredProps.includes(p) && !Number.isInteger(Number.parseFloat(v))) v = measured
          parts.push(omit[p] !== undefined && v === omit[p] ? '' : v)
        }
        ;(seen[key] ??= new Set()).add(parts.join('|'))
      }
      const out: Record<string, string[]> = {}
      for (const [k, v] of Object.entries(seen)) out[k] = Array.from(v).sort()
      return out
    },
    {
      props: PROPS as unknown as string[],
      omit: OMIT,
      semanticTags: SEMANTIC_TAGS,
      measuredProps: MEASURED_IF_FRACTIONAL,
      measured: MEASURED,
    },
  )
}

/** Renders one serialised record as `prop: value` pairs, for a failure message. */
function explain(record: string): string {
  return record
    .split('|')
    .map((v, i) => (v === '' ? null : `${PROPS[i]}: ${v}`))
    .filter(Boolean)
    .join(', ')
}

function loadBaseline(): Baseline {
  if (!existsSync(BASELINE_FILE)) return {}
  return JSON.parse(readFileSync(BASELINE_FILE, 'utf-8')) as Baseline
}

/**
 * Collected across the whole file and written once in `afterAll`. Playwright
 * runs this spec with `workers: 1` (playwright.config.ts), so a module-level
 * accumulator is safe here and is the only way to produce one committed file
 * from tests that each own a different surface.
 */
const collected: Baseline = {}
const mismatches: string[] = []
const missing: string[] = []

/**
 * The theme is applied by writing `data-theme` on the root element, which is
 * exactly what `useTheme()` does (`useTheme.ts:31`). Setting it directly avoids
 * a reload per theme; nothing reads the attribute back, so no state diverges.
 */
async function setTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
  await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme)
}

/**
 * Waits until the element count stops moving.
 *
 * A visible `<main>` is not a settled page: several surfaces render a
 * conditional section only once its own query resolves (the project members
 * invite card is the one that caught this), and a capture taken before then
 * records a baseline the next run cannot reproduce. Element count is the
 * cheapest signal that covers all of them at once, including the ones this
 * spec does not know about.
 */
async function waitForStableDom(page: Page): Promise<void> {
  let last = -1
  let stable = 0
  for (let i = 0; i < 40 && stable < 3; i++) {
    await page.waitForTimeout(250)
    const n = await page.evaluate(() => document.querySelectorAll('*').length)
    stable = n === last ? stable + 1 : 0
    last = n
  }
}

const VIEWPORTS = [
  { name: '1440x900', width: 1440, height: 900 },
  { name: '375x812', width: 375, height: 812 },
] as const

interface SurfaceOptions {
  /** The surface's own readiness signal. Without one the capture races the
   *  fetch and records a skeleton, which then compares clean against a second
   *  skeleton and proves nothing. */
  settle: string
  /** Drives the surface into a state a plain navigation does not reach - an
   *  open modal, an open drawer. Runs after `settle`, before the capture. */
  prepare?: (page: Page) => Promise<void>
  /** Narrows the viewport set for a surface that only exists at one width. */
  viewports?: readonly { name: string; width: number; height: number }[]
}

/**
 * Captures one surface across both viewports and both themes, and either
 * records or asserts each combination.
 */
async function snapshotSurface(page: Page, id: string, path: string, opts: SurfaceOptions): Promise<void> {
  const baseline = loadBaseline()

  for (const vp of opts.viewports ?? VIEWPORTS) {
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await page.goto(path)
    await expect(page.locator(opts.settle).first()).toBeVisible({ timeout: 30_000 })
    await waitForStableDom(page)
    if (opts.prepare) {
      await opts.prepare(page)
      await waitForStableDom(page)
    }

    for (const theme of ['light', 'dark'] as const) {
      await setTheme(page, theme)
      const slot = `${id} @${vp.name} ${theme}`
      const snap = await capture(page)

      if (UPDATE) {
        collected[slot] = snap
        continue
      }

      const before = baseline[slot]
      expect(before, `no baseline for "${slot}" - rerun with UPDATE_VISUAL_BASELINE=1`).toBeTruthy()

      // An absent signature is not evidence of a style change - it is evidence
      // the element was not on the page, which a slow query or a timed state
      // can both produce (the landing intro's skip hint fades in and back out;
      // a settings section renders only once its fetch lands). One re-capture
      // settles both. A *differing value* is never retried: nothing about
      // waiting longer can change what a declaration computes to.
      let second: Snapshot | null = null
      if (Object.keys(before).some((sig) => !snap[sig])) {
        await waitForStableDom(page)
        second = await capture(page)
      }

      for (const [sig, records] of Object.entries(before)) {
        const now = new Set([...(snap[sig] ?? []), ...(second?.[sig] ?? [])])
        if (now.size === 0) {
          missing.push(`${slot} :: ${sig}`)
          continue
        }
        // A record the baseline holds and this run does not is a style that
        // moved. An extra record in this run is not: seeded data grows across
        // a suite run, so a page can legitimately render a state the baseline
        // never saw. The direction that matters is the one that can only be
        // caused by the diff.
        for (const rec of records) {
          if (!now.has(rec)) {
            mismatches.push(
              `${slot} :: ${sig}\n      expected: ${explain(rec)}\n      observed: ${[...now]
                .map(explain)
                .join(' / ')}`,
            )
          }
        }
      }
    }
  }
}

test.describe('Computed-style parity across the component surface set', () => {
  test.describe.configure({ timeout: 180_000 })

  const projectId = env('E2E_PROJECT_ID')
  const orgId = env('E2E_ORG_ID')
  const agentId = env('E2E_AGENT_ID')
  const chatroomId = env('E2E_CHATROOM_ID')
  const workspaceId = env('E2E_WORKSPACE_ID')
  const keyGroupId = env('E2E_KEY_GROUP_ID')

  test('the agent list surface', async ({ authedPage: page }) => {
    test.skip(!projectId, 'needs a seeded project')
    await snapshotSurface(page, 'agents-list', `/projects/${projectId}/agents`, { settle: 'main#main-content' })
  })

  test('the agent detail surface', async ({ authedPage: page }) => {
    test.skip(!agentId, 'needs a seeded agent')
    await snapshotSurface(page, 'agent-detail', `/agents/${agentId}`, { settle: 'main#main-content h1' })
  })

  test('the account key list surface', async ({ authedPage: page }) => {
    await snapshotSurface(page, 'keys', '/keys', { settle: 'main#main-content' })
  })

  test('the key group detail surface', async ({ authedPage: page }) => {
    test.skip(!projectId || !keyGroupId, 'needs a seeded key group')
    await snapshotSurface(
      page,
      'key-group-detail',
      `/projects/${projectId}/key-groups/${keyGroupId}`,
      { settle: 'main#main-content' },
    )
  })

  test('the org list surface', async ({ authedPage: page }) => {
    await snapshotSurface(page, 'orgs', '/orgs', { settle: 'main#main-content' })
  })

  test('the org detail surface', async ({ authedPage: page }) => {
    test.skip(!orgId, 'needs a seeded org')
    await snapshotSurface(page, 'org-detail', `/orgs/${orgId}`, { settle: 'main#main-content' })
  })

  test('the project members surface', async ({ authedPage: page }) => {
    test.skip(!projectId, 'needs a seeded project')
    await snapshotSurface(page, 'project-members', `/projects/${projectId}/members`, { settle: 'main#main-content' })
  })

  test('the chatroom surface', async ({ authedPage: page }) => {
    test.skip(!chatroomId, 'needs a seeded chatroom')
    await snapshotSurface(page, 'chatroom', `/chatrooms/${chatroomId}`, { settle: 'main#main-content' })
  })

  test('the chatroom settings surface', async ({ authedPage: page }) => {
    test.skip(!chatroomId, 'needs a seeded chatroom')
    await snapshotSurface(page, 'chatroom-settings', `/chatrooms/${chatroomId}/settings`, { settle: 'main#main-content' })
  })

  test('the workflow list surface', async ({ authedPage: page }) => {
    test.skip(!workspaceId, 'needs a seeded workspace')
    await snapshotSurface(page, 'workflows', `/workspaces/${workspaceId}/workflows`, { settle: 'main#main-content' })
  })

  test('the profile surface', async ({ authedPage: page }) => {
    await snapshotSurface(page, 'profile', '/account/profile', { settle: 'main#main-content' })
  })

  test('the notifications surface', async ({ authedPage: page }) => {
    await snapshotSurface(page, 'notifications', '/notifications', { settle: 'main#main-content' })
  })

  test('the invites surface', async ({ authedPage: page }) => {
    await snapshotSurface(page, 'invites', '/invites', { settle: 'main#main-content' })
  })

  test('the admin metrics surface', async ({ adminPage: page }) => {
    await snapshotSurface(page, 'admin-metrics', '/admin/metrics', { settle: 'main#main-content' })
  })

  // Three surfaces added for coverage rather than for their own sake: they are
  // the cheapest routes to SCheckbox, STextarea and SAccordion, none of which
  // any of the surfaces above renders.
  test('the delete account surface', async ({ authedPage: page }) => {
    await snapshotSurface(page, 'account-delete', '/account/delete', { settle: 'main#main-content' })
  })

  test('the prompt assistant surface', async ({ authedPage: page }) => {
    await snapshotSurface(page, 'prompt-assistant', '/account/prompt-assistant', {
      settle: 'main#main-content',
    })
  })

  test('the agent tools surface', async ({ authedPage: page }) => {
    test.skip(!agentId, 'needs a seeded agent')
    await snapshotSurface(page, 'agent-tools', `/agents/${agentId}/tools`, { settle: 'main#main-content' })
  })

  // Two surfaces a navigation cannot reach. Between them they add SModal,
  // STextarea, SToggle-in-a-dialog and SDrawer to the covered set; without
  // them four overlay primitives would rest on value equality alone.
  test('the chatroom create modal', async ({ authedPage: page }) => {
    test.skip(!workspaceId, 'needs a seeded workspace')
    await snapshotSurface(page, 'chatroom-create-modal', `/workspaces/${workspaceId}/chatrooms`, {
      settle: 'main#main-content',
      prepare: async (p) => {
        await p.locator('main#main-content .s-btn--primary').first().click()
        await expect(p.locator('.s-modal__panel')).toBeVisible({ timeout: 10_000 })
      },
    })
  })

  test('the mobile sidebar drawer', async ({ authedPage: page }) => {
    test.skip(!projectId, 'needs a seeded project')
    await snapshotSurface(page, 'mobile-drawer', `/projects/${projectId}/agents`, {
      viewports: [{ name: '375x812', width: 375, height: 812 }],
      settle: 'main#main-content',
      prepare: async (p) => {
        await p.locator('.topbar__sidebar-toggle').click()
        // toBeVisible() is true from the first frame of the 300ms slide, while
        // the panel is still translateX(-100%) - see 23-mobile-viewport.spec.ts.
        // Nothing here reads a box, but capturing mid-transition would record a
        // transform-dependent state, so settle it the same way.
        await expect
          .poll(async () => (await p.locator('.s-drawer__panel--sm').boundingBox())?.x, { timeout: 5_000 })
          .toBe(0)
      },
    })
  })

  test('the landing surface', async ({ page }) => {
    await snapshotSurface(page, 'landing', '/', {
      settle: 'body',
      // Dismiss the intro curtain before capturing. Two reasons: it covers the
      // page, so without this the surface records the curtain and none of
      // Landing.vue; and its skip hint is a timed state (LandingIntro.vue:140),
      // so whether it is on depends on where in the animation the capture lands.
      prepare: async (p) => {
        await p.keyboard.press('Escape')
        await expect(p.locator('.intro')).toHaveCount(0, { timeout: 15_000 })
      },
    })
  })

  test('the login surface', async ({ page }) => {
    await snapshotSurface(page, 'login', '/login', { settle: 'form' })
  })

  test.afterAll(() => {
    if (UPDATE) {
      mkdirSync(BASELINE_DIR, { recursive: true })
      const ordered: Baseline = {}
      for (const k of Object.keys(collected).sort()) ordered[k] = collected[k]
      writeFileSync(BASELINE_FILE, `${JSON.stringify(ordered, null, 1)}\n`, 'utf-8')
      console.log(`[visual-parity] wrote ${Object.keys(ordered).length} snapshots to ${BASELINE_FILE}`)
      return
    }
    // Reported once rather than per surface: a token substitution that lands in
    // the wrong rule typically moves dozens of elements, and failing on the
    // first one hides the shape of the mistake.
    const problems: string[] = []
    if (missing.length) {
      problems.push(
        `${missing.length} baseline signature(s) no longer render:\n  ${missing.slice(0, 40).join('\n  ')}`,
      )
    }
    if (mismatches.length) {
      problems.push(
        `${mismatches.length} computed-style difference(s):\n  ${mismatches.slice(0, 60).join('\n  ')}`,
      )
    }
    expect(problems.join('\n\n'), 'AC-1: no rendered difference').toBe('')
  })
})
