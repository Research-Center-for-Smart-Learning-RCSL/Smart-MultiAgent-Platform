import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * T-2 of `docs/tasks/2026-08-21-visual-refinement-phase1-token-adoption`, and
 * the criterion AC-3 is measured by.
 *
 * The motivating defect was not that the values were wrong - it was that
 * `main.css` declared itself the single source of truth for them while 900-odd
 * declarations elsewhere re-stated the numbers literally, so editing a token
 * changed nothing visible. Fixing the call sites without pinning the rule just
 * restarts the clock: the next component is written the same way. This sweep is
 * the pin.
 *
 * It is a Vitest test standing in for a lint rule, which is the same compromise
 * `2026-08-19-content-area-spacing-and-scroll-contract`'s FU-6 records for its
 * view-root sweep. There are now two of them, which is the condition that
 * dossier set for writing the ESLint rule instead (this dossier's FU-2).
 */

// The glob supplies the file list; the contents come from disk. Under `?raw` a
// .vue file arrives verbatim but a .css file does not - @tailwindcss/vite
// claims it and delivers its processed output, in which main.css's @layer base
// and @utility blocks no longer look like what was written. Reading every file
// the same way removes the asymmetry rather than special-casing three paths.
const paths = Object.keys(
  import.meta.glob(['/src/**/*.vue', '/src/**/*.css'], { eager: true, query: '?raw' }),
)
const sources: Record<string, string> = Object.fromEntries(
  paths.map((p) => [p, readFileSync(resolve(process.cwd(), p.slice(1)), 'utf-8')]),
)

/** The Q-2 property set: the shared vocabulary that must move together. */
const PROPS = [
  'font-size',
  'font-weight',
  'line-height',
  'padding',
  'padding-top',
  'padding-right',
  'padding-bottom',
  'padding-left',
  'padding-inline',
  'padding-block',
  'margin',
  'margin-top',
  'margin-right',
  'margin-bottom',
  'margin-left',
  'margin-inline',
  'margin-block',
  'gap',
  'row-gap',
  'column-gap',
]

/** Properties whose value is a bare number rather than a length. */
const UNITLESS = new Set(['font-weight', 'line-height'])

/**
 * Declarations that keep a literal, each with the reason it is not a token.
 * Keyed `path:property:value` rather than by line, so the list does not rot
 * every time a rule moves. Adding a row here is a decision; the reason column
 * is what makes it reviewable.
 */
const EXEMPT: Record<string, string> = {}

function exempt(reason: string, ...keys: string[]): void {
  for (const k of keys) EXEMPT[k] = reason
}

// Q-3: three sizes that no document names, that nobody chose, and that no
// token covers. 0.7rem is 11.2px and 0.6875rem is 11px - 0.2px apart, which is
// the strongest evidence neither was a decision. Snapping either to the nearest
// token would change a rendered pixel, which AC-1 forbids. Phase 2 decides
// whether an 11px step is real (FU-1).
exempt(
  'Q-3: an undocumented one-off size; phase 2 decides whether it joins the ramp (FU-1)',
  'shared/ui/LocaleToggle.vue:font-size:0.6875rem',
  'shared/ui/STabs.vue:font-size:0.7rem',
  'shared/ui/SIdleDialog.vue:font-size:0.9375rem',
  'app/components/AppSidebar.vue:font-size:11px',
  'app/components/OrgProjectSwitcher.vue:font-size:0.6875rem',
  'app/components/SidebarChatroomList.vue:font-size:11px',
  'app/components/SidebarGroup.vue:font-size:11px',
  'slices/conversation/components/ChatroomAgentSidebar.vue:font-size:11px',
  'slices/conversation/components/ChatroomMessageBubble.vue:font-size:11px',
  'slices/conversation/components/ChatroomPresence.vue:font-size:11px',
  'slices/conversation/components/ObservationCard.vue:font-size:11px',
  'slices/conversation/components/ObserverPanel.vue:font-size:11px',
  'slices/workflow/components/WorkflowNodeComponent.vue:font-size:0.5625rem',
)

// Line heights off the 1 / 1.3 / 1.4 / 1.5 / 1.6 ladder, and two written as a
// length. Same rule as above: a token would assert a decision nobody made.
exempt(
  'Q-3: a line height off the ladder; no token asserts it and snapping would move a pixel',
  'shared/ui/SButton.vue:line-height:1.25',
  'shared/ui/SWakeupEditor.vue:line-height:1.35',
  'shared/ui/SAlert.vue:line-height:24px',
  'slices/notifications/components/NotificationBell.vue:line-height:18px',
  'app/views/Landing.vue:line-height:1.1',
  'app/views/Landing.vue:line-height:1.55',
)

// The landing page carries its own marketing type scale, which is deliberate:
// it is the one surface that is not the product UI. Phase 2 owns whether it
// converges on the ramp.
exempt(
  'a marketing-page type scale that deliberately sits outside the product ramp',
  'app/views/Landing.vue:font-size:clamp(2rem, 4vw, 3rem)',
  'app/views/Landing.vue:font-size:1.0625rem',
  'app/views/Landing.vue:font-size:0.9375rem',
  'app/components/BrandLogo.vue:font-size:26px',
  'app/components/BrandLogo.vue:font-size:22px',
  'slices/admin/views/AdminHomeView.vue:font-size:2rem',
  'slices/admin/views/AdminMetricsView.vue:font-size:2rem',
)

// Q-2: geometry local to one component. A negative single pixel pulls an
// element over its own border; tokenising it would add an indirection with one
// consumer and no future edit that would use it.
exempt(
  'Q-2: a single-pixel offset that pulls an element over its own border',
  'shared/ui/SCheckbox.vue:margin:-1px',
  'shared/ui/SRadio.vue:margin:-1px',
  'shared/ui/STable.vue:margin:-1px',
  'shared/ui/STabs.vue:margin:-1px',
  'shared/ui/STabs.vue:margin-bottom:-1px',
  'shared/ui/SInput.vue:margin:-6px -4px -6px 0',
  'shared/styles/main.css:margin:-1px',
)

// Negative pull-ups that tighten one element against the one above it. Same
// reasoning as the border offsets: a token here would have one consumer.
exempt(
  'Q-2: a negative offset that tightens spacing against the element above',
  'slices/identity/views/LoginView.vue:margin-top:-8px',
  'slices/identity/views/RegisterView.vue:margin:-8px 0 0',
  'slices/tenancy/views/ProjectMembersView.vue:margin-top:-0.5rem',
)

// A shorthand whose parts do not all have tokens. Splitting it into longhands
// so half could be tokenised would restructure a rule to hide a literal, which
// is worse than stating it here.
exempt(
  'a shorthand mixing a tokenised value with one that has no token',
  'shared/ui/SWakeupEditor.vue:padding:12px 14px',
  'slices/conversation/components/ChatroomMessageBubble.vue:padding:12px 14px',
  'slices/conversation/components/ObservationCard.vue:padding:1px 8px',
  'app/views/Landing.vue:padding:48px 0 64px',
  'app/views/Landing.vue:padding:32px 0 72px',
  'app/views/Landing.vue:padding:8px 0 72px',
  'app/views/Landing.vue:padding:16px 18px',
  'app/views/Landing.vue:margin:28px 0 0',
)

// Sizes off the 4px grid that no token covers. 13px and 26px are the mono type
// step and its double, borrowed as a space; 28px and 64px are one-offs. All
// four would need a token with a single consumer, which is the failure mode
// Q-2 names.
exempt(
  'a one-off size with no token on the spacing ladder',
  'app/ErrorBoundary.vue:margin:4rem auto',
  'app/components/LandingIntro.vue:gap:28px',
  'app/components/SidebarChatroomList.vue:padding-left:13px',
  'slices/conversation/components/ObservationReleaseDialog.vue:margin-left:26px',
)

/**
 * Strips `@font-face { ... }` blocks, brace-counted.
 *
 * A font-face descriptor is not a component style: its `font-weight` is the
 * variable font's weight *axis range* (`100 900`), which shares a name with the
 * property this sweep governs and means something else entirely. Excluded
 * structurally rather than by exemption, so a later descriptor inherits the
 * rule instead of needing its own licence.
 */
function stripFontFace(css: string): string {
  let out = ''
  let i = 0
  for (;;) {
    const at = css.indexOf('@font-face', i)
    if (at === -1) return out + css.slice(i)
    out += css.slice(i, at)
    const open = css.indexOf('{', at)
    if (open === -1) return out
    let depth = 0
    let j = open
    for (; j < css.length; j++) {
      if (css[j] === '{') depth++
      else if (css[j] === '}' && --depth === 0) break
    }
    i = j + 1
  }
}

/** CSS regions of a file: every `<style>` block, or the whole of a .css file. */
function cssRegions(text: string, path: string): string[] {
  if (path.endsWith('.css')) return [stripFontFace(text)]
  const out: string[] = []
  const re = /<style[^>]*>/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    const start = m.index + m[0].length
    const end = text.indexOf('</style>', start)
    if (end !== -1) out.push(text.slice(start, end))
  }
  return out
}

/**
 * True when a declaration still states a size itself.
 *
 * `var()` and `env()` are removed first: the former is the point of the
 * exercise, and the latter's `0px` fallback is a "no inset" sentinel rather
 * than a size. What survives is split on whitespace and on the arithmetic that
 * `calc()` allows, so `calc(var(--card-pad) * -1)`'s multiplier is not mistaken
 * for a length - it carries no unit.
 */
function statesALiteral(prop: string, value: string): boolean {
  const stripped = value.replace(/var\([^()]*\)/g, ' ').replace(/env\([^()]*\)/g, ' ')
  const parts = stripped.split(/[\s,()*/+]+/).filter(Boolean)
  for (const p of parts) {
    const withUnit = /^-?\d*\.?\d+(px|rem|em|ch|%|vh|vw|vmin|vmax)$/.test(p)
    const bare = UNITLESS.has(prop) && /^-?\d*\.?\d+$/.test(p)
    if ((withUnit || bare) && Number.parseFloat(p) !== 0) return true
  }
  return false
}

/**
 * Every declaration of a Q-2 property across the whole tree, as
 * `{ key, prop, value }`. Both checks below read from this one walk - they used
 * to build the property alternation separately, and had already drifted (one
 * sorted it by length, the other did not), which is the shape of bug a sweep
 * can least afford: the exemption check and the sweep must agree on what
 * counts as a declaration or an exemption can go stale without being noticed.
 */
function* declarations(): Generator<{ key: string; prop: string; value: string }> {
  const alternation = [...PROPS].sort((a, b) => b.length - a.length).join('|')
  for (const [path, text] of Object.entries(sources)) {
    const rel = path.replace(/^\/src\//, '')
    if (rel.includes('__tests__/')) continue
    for (const css of cssRegions(text, path)) {
      const re = new RegExp(`^[ \\t]*(${alternation})\\s*:\\s*([^;{}]+);`, 'gm')
      let m: RegExpExecArray | null
      while ((m = re.exec(css)) !== null) {
        const prop = m[1]
        const value = m[2].replace(/\s+/g, ' ').trim()
        yield { key: `${rel}:${prop}:${value}`, prop, value }
      }
    }
  }
}

function sweep(): { offences: string[]; scanned: number; tokenised: number; live: Set<string> } {
  const offences: string[] = []
  const live = new Set<string>()
  let scanned = 0
  let tokenised = 0

  for (const { key, prop, value } of declarations()) {
    scanned++
    live.add(key)
    if (value.includes('var(--')) tokenised++
    if (!statesALiteral(prop, value)) continue
    if (key in EXEMPT) continue
    offences.push(key)
  }
  return { offences, scanned, tokenised, live }
}

const result = sweep()

describe('component styles state no size of their own', () => {
  it('scanned a plausible amount of CSS', () => {
    // Without this the whole suite passes vacuously the day the glob stops
    // matching - which is the failure mode a source sweep is most prone to,
    // because nothing else in the build depends on it resolving.
    // At the time of writing: 220 files, 985 declarations in the property set,
    // 811 of them naming a token, 48 exempt below, and the rest stating `0`,
    // `auto` or an env() inset - no size at all.
    expect(Object.keys(sources).length).toBeGreaterThan(200)
    expect(result.scanned).toBeGreaterThan(900)
    expect(result.tokenised).toBeGreaterThan(750)
  })

  it('leaves no literal font-size, font-weight, line-height, padding, margin or gap', () => {
    expect(result.offences).toEqual([])
  })

  it('carries no exemption for a declaration that no longer exists', () => {
    // An exemption that stops matching is a licence nobody is using, and it
    // would silently re-permit the literal if the rule ever came back.
    expect(Object.keys(EXEMPT).filter((k) => !result.live.has(k))).toEqual([])
  })
})
