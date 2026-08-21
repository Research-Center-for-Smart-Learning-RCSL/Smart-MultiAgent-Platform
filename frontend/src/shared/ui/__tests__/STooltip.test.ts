import { describe, expect, it } from 'vitest'

import { declaration, readComponentStyles, topLevelRule } from '../../../../tests/utils'

describe('STooltip stacking', () => {
  // F-50: a literal 50 against --z-tooltip: 600. Latent today, because
  // main.app-shell__content clips the tooltip long before it could reach the
  // top bar (200) or the network banner (350) - it becomes live the moment a
  // tooltip sits in a container that does not clip it.
  it('resolves its layer from the documented scale, not a literal', () => {
    const rule = topLevelRule(readComponentStyles('shared/ui/STooltip.vue'), '.s-tooltip')
    expect(rule).not.toBeNull()

    const zIndex = declaration(rule as string, 'z-index')
    expect(zIndex).toBe('var(--z-tooltip)')
  })

  // AC-11: STable.vue:491's 10 is legitimate (the thead against its own rows,
  // inside the table's own stacking context) and is the only literal that may
  // remain in shared/ui.
  it('leaves STable as the only numeric z-index in the overlay set', () => {
    for (const component of ['SDropdown', 'SModal', 'SDrawer', 'STooltip', 'SEmptyState']) {
      const css = readComponentStyles(`shared/ui/${component}.vue`)
      const literals = [...css.matchAll(/z-index:\s*(\d+)/g)].map((m) => m[1])
      expect(literals, `${component}.vue declares a numeric z-index`).toEqual([])
    }
  })
})
