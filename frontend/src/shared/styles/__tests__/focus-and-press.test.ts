import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { declaration, readComponentStyles, topLevelRule } from '../../../../tests/utils'

/**
 * AC-4 and AC-5 of
 * `docs/tasks/2026-08-21-visual-refinement-phase2-identity-and-depth`.
 *
 * Two changes that are invisible until they are wrong.
 *
 * The focus ring moved from a two-layer box-shadow to a real outline, and the
 * 29 component rules that restated the old ring all carried `outline: none`.
 * Left in place, each of those would now *suppress* the global indicator rather
 * than duplicate it - a keyboard user simply loses the control. That is an
 * accessibility regression with no visual symptom for anyone testing with a
 * mouse, so it is asserted rather than reviewed.
 *
 * And `:active` appeared nowhere in `frontend/src` before this, so a button
 * being held down was pixel-identical to one merely hovered.
 */
const MAIN_CSS = resolve(process.cwd(), 'src/shared/styles/main.css')
const mainCss = readFileSync(MAIN_CSS, 'utf-8')

const sources = import.meta.glob('/src/**/*.vue', { eager: true, query: '?raw', import: 'default' }) as Record<
  string,
  string
>

describe('focus indicator', () => {
  it('is a real outline in one global rule', () => {
    expect(mainCss).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--color-accent\)/)
    expect(mainCss).toMatch(/:focus-visible\s*\{[^}]*outline-offset:\s*2px/)
  })

  it('no longer defines --focus-ring anywhere', () => {
    // The old ring painted its inner layer in var(--color-bg) whatever was
    // actually behind the element, which is the defect the outline replaces.
    // A surviving declaration would invite a component to reach for it again.
    expect(mainCss).not.toContain('--focus-ring')

    const offenders = Object.entries(sources)
      .filter(([path]) => !path.includes('__tests__/'))
      .filter(([, text]) => text.includes('--focus-ring'))
      .map(([path]) => path)
    expect(offenders).toEqual([])
  })

  /**
   * The dangerous leftover. A component may suppress the outline only when
   * something else draws the ring for that element - a clipped native input
   * whose visible sibling carries it, a wrapper on :focus-within, or a
   * container that is focused programmatically and should show nothing.
   *
   * Every entry here is a deliberate exception with a stated reason. A new
   * `outline: none` that is not on this list fails, which is the point.
   */
  const SUPPRESSION_ALLOWLIST = new Set([
    // Ring drawn by the wrapper on :focus-within (SInput, SSearchInput).
    '.s-input__field',
    '.search-input__field',
    // Focused programmatically, never by a keyboard traversal.
    '.switcher__panel:focus',
    '.app-shell__content:focus-visible',
    '.s-drawer__panel',
    '.s-modal__panel',
    // tabindex="-1" purely as the chatroom surface coordinator's focus
    // fallback; a ring around the whole room reads as a rendering fault.
    '.chatroom:focus-visible',
    // Paired with an .s-alert:focus-visible rule that restores the ring.
    '.s-alert:focus',
    // Pointer focus only, so the keyboard ring survives. The unscoped form of
    // each of these was a real suppression before phase 2.
    '.code-editor:focus:not(:focus-visible)',
    '.composer__textarea:focus:not(:focus-visible)',
    '.s-dropdown__item:focus:not(:focus-visible)',
  ])

  it('suppresses the outline only where something else draws the ring', () => {
    const offenders: string[] = []
    for (const [path, text] of Object.entries(sources)) {
      if (path.includes('__tests__/')) continue
      const styles = [...text.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')
      // Selector immediately preceding a rule body that zeroes the outline.
      for (const m of styles.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        const selector = m[1].trim().split('\n').pop()?.trim() ?? ''
        if (!/outline:\s*none/.test(m[2])) continue
        if (SUPPRESSION_ALLOWLIST.has(selector)) continue
        offenders.push(`${path.replace('/src/', '')}: ${selector}`)
      }
    }
    expect(offenders).toEqual([])
  })
})

describe('press state', () => {
  const VARIANTS = ['primary', 'secondary', 'danger', 'ghost', 'link'] as const

  it.each(VARIANTS)('SButton %s changes under the pointer', (variant) => {
    const css = readComponentStyles('shared/ui/SButton.vue')
    const rule = topLevelRule(css, `.s-btn--${variant}:active:not(.s-btn--disabled)`)
    expect(rule, `.s-btn--${variant} has no :active rule, so holding it looks like hovering it`)
      .not.toBeNull()
  })

  it('depresses every variant that is shaped like a button', () => {
    const css = readComponentStyles('shared/ui/SButton.vue')
    const base = topLevelRule(css, '.s-btn:active:not(.s-btn--disabled):not(.s-btn--link)')
    expect(base).not.toBeNull()
    expect(declaration(base as string, 'transform')).toBe('translateY(1px)')
  })

  it('transitions the properties the press actually changes', () => {
    // The press moves the button and drops its elevation. Without both in the
    // transition list the depression jumps rather than animating, and the
    // reduced-motion freeze has nothing to collapse.
    const transition = declaration(
      topLevelRule(readComponentStyles('shared/ui/SButton.vue'), '.s-btn') as string,
      'transition',
    )
    expect(transition).toContain('transform')
    expect(transition).toContain('box-shadow')
  })

  // Phase 3's FU-12. The press drops to `--elevation-0`, which resolves to
  // `none` - so without a resting shadow to fall from, half the press language
  // was declared and none of it rendered. Pinned per variant because the answer
  // differs per variant: `ghost` and `link` are transparent text and are meant
  // to stay flat.
  it.each(['primary', 'secondary', 'danger'] as const)(
    'SButton %s rests at an elevation the press can drop from',
    (variant) => {
      const rule = topLevelRule(readComponentStyles('shared/ui/SButton.vue'), `.s-btn--${variant}`)
      expect(rule, `.s-btn--${variant} has no base rule`).not.toBeNull()
      expect(
        declaration(rule as string, 'box-shadow'),
        `.s-btn--${variant} declares no resting shadow, so :active's --elevation-0 is a no-op`,
      ).toBe('var(--elevation-1)')
    },
  )

  it.each(['ghost', 'link'] as const)('SButton %s stays flat at rest', (variant) => {
    const rule = topLevelRule(readComponentStyles('shared/ui/SButton.vue'), `.s-btn--${variant}`)
    expect(declaration(rule as string, 'box-shadow')).toBeNull()
  })

  it('takes the elevation back off a disabled button', () => {
    // Elevation is the signal that a control can be pressed, so one that cannot
    // must not advertise it. The `:hover`/`:active` rules all carry
    // `:not(.s-btn--disabled)`; the resting rule lives on the variant and
    // cannot, so this is where the exception has to hold.
    const rule = topLevelRule(readComponentStyles('shared/ui/SButton.vue'), '.s-btn--disabled')
    expect(rule).not.toBeNull()
    expect(declaration(rule as string, 'box-shadow')).toBe('none')
  })

  it('gives the shell chrome the same press language', () => {
    expect(
      topLevelRule(readComponentStyles('app/components/AppTopBar.vue'), '.topbar__sidebar-toggle:active'),
    ).not.toBeNull()
    expect(
      topLevelRule(readComponentStyles('app/components/SidebarNavItem.vue'), '.nav-item:active'),
    ).not.toBeNull()
  })

  it('is present somewhere in the source at all', () => {
    // Before this dossier a repository-wide grep for `:active` returned nothing.
    const withActive = Object.entries(sources).filter(([, text]) => /:active\b/.test(text))
    expect(withActive.length).toBeGreaterThan(0)
  })
})
