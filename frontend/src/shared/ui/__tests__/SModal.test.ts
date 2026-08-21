import { describe, expect, it } from 'vitest'

import {
  atRuleBody,
  declaration,
  readComponentStyles,
  topLevelRule,
} from '../../../../tests/utils'

const css = (): string => readComponentStyles('shared/ui/SModal.vue')

describe('SModal viewport fit', () => {
  // F-41: .s-modal centred a flex item in a container that neither scrolled
  // nor padded, while only .s-modal__body was height-capped (70vh) - the
  // header and footer were unbounded. A centred flex item taller than its
  // container overflows equally in both directions, and the part above y = 0
  // is unreachable because neither .s-modal nor body scrolls. The clipped
  // element is the dialog's aria-labelledby target.
  //
  // Structural guards: jsdom evaluates no viewport units and performs no
  // layout, so 844x390 and 200% zoom are manual checks (V per §8).
  it('scrolls rather than clipping when the panel exceeds the viewport', () => {
    const rule = topLevelRule(css(), '.s-modal')
    expect(rule).not.toBeNull()
    expect(declaration(rule as string, 'overflow-y')).toBe('auto')
    // flex-start plus margin: auto on the panel centres it in a scroll
    // container without the top-clipping align-items: center produces there.
    expect(declaration(rule as string, 'align-items')).toBe('flex-start')
    // What F-41 needs is that the panel is never flush against the viewport
    // edge, which the literal 24px used to say. The safe-area work (F-25) made
    // each side max(24px, inset): still >= the gutter everywhere, and larger
    // only where a cutout demands it. The gutter is now --space-6, whose value
    // is pinned at 24px by shared/styles/__tests__/tokens.test.ts, so the
    // guarantee is unchanged and is asserted in the layer that owns it.
    const padding = declaration(rule as string, 'padding') ?? ''
    for (const side of ['top', 'right', 'bottom', 'left']) {
      expect(padding).toContain(`max(var(--space-6), env(safe-area-inset-${side}, 0px))`)
    }
  })

  it('centres the panel with auto margins, which a scroll container respects', () => {
    const rule = topLevelRule(css(), '.s-modal__panel')
    expect(rule).not.toBeNull()
    expect(declaration(rule as string, 'margin')).toBe('auto')
  })

  // The new padding would otherwise put a 24px border around every modal below
  // 768px, where the design is deliberately edge-to-edge full screen.
  it('drops the padding in the mobile full-screen branch', () => {
    const mobile = atRuleBody(css(), 'max-width: 767px')
    expect(mobile).not.toBeNull()

    const rule = topLevelRule(mobile as string, '.s-modal')
    expect(rule).not.toBeNull()
    expect(declaration(rule as string, 'padding')).toBe('0')
  })
})
