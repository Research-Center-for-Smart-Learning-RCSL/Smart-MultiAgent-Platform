import { describe, expect, it } from 'vitest'

import { declaration, readComponentStyles, topLevelRule } from '../../../../tests/utils'

// Structural guards only — see tests/utils/styleSource.ts. That the banner
// actually stops covering the top bar is T-11 (Playwright), because it is a
// geometry and jsdom has no layout engine.
describe('ImpersonationBanner layout contract', () => {
  const rule = (): string => {
    const body = topLevelRule(
      readComponentStyles('slices/admin/components/ImpersonationBanner.vue'),
      '.impersonation-banner',
    )
    expect(body).not.toBeNull()
    return body as string
  }

  // The defect: `position: fixed` takes the banner out of flow, so nothing
  // downstream reserves its ~33px and it paints over the 56px top bar.
  it('is in flow, so it reserves its own vertical space', () => {
    const body = rule()
    expect(declaration(body, 'position')).toBe('sticky')
    expect(declaration(body, 'top')).toBe('0')
    // A sticky block-level element already spans its container; the fixed
    // positioning's left/right pair has no job left to do.
    expect(declaration(body, 'left')).toBeNull()
    expect(declaration(body, 'right')).toBeNull()
  })

  // A guard, not a regression test: 2026-08-19-transient-feedback-channels
  // already tokenised this (it was the literal 9999), and the toaster contract
  // at --z-toast: 500 depends on the banner staying below it.
  it('draws on the documented banner layer, never a literal', () => {
    const zIndex = declaration(rule(), 'z-index')
    expect(zIndex).toMatch(/var\(--z-banner/)
    expect(zIndex).not.toMatch(/^\d/)
  })
})
