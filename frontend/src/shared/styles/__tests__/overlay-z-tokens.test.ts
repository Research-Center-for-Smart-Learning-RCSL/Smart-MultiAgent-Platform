import { describe, expect, it } from 'vitest'

import {
  declaration,
  readComponentSource,
  readComponentStyles,
  topLevelRule,
} from '../../../../tests/utils'

// Overlays that live in the root stacking context must take their layer from
// the documented scale in main.css. A raw literal (20, z-50) sits below every
// token and gets painted under whichever tokenised overlay it meets — the
// mention menu lost to the compact agents-rail overlay exactly this way.
describe('slice overlays resolve their layer from the token scale', () => {
  it('mention autocomplete sits on the dropdown layer', () => {
    const rule = topLevelRule(
      readComponentStyles('slices/conversation/components/ChatroomComposer.vue'),
      '.composer__mentions',
    )
    expect(rule).not.toBeNull()
    expect(declaration(rule as string, 'z-index')).toBe('var(--z-dropdown)')
  })

  it('workflow node palette sits on the dropdown layer and caps its height', () => {
    const rule = topLevelRule(
      readComponentStyles('slices/workflow/views/WorkflowEditorView.vue'),
      '.wf-palette',
    )
    expect(rule).not.toBeNull()
    expect(declaration(rule as string, 'z-index')).toBe('var(--z-dropdown)')
    // Without a height cap + own scrollbar, the ~400px palette gets its tail
    // clipped by the shell scrollport on short windows.
    expect(declaration(rule as string, 'overflow-y')).toBe('auto')
    expect(declaration(rule as string, 'max-height')).not.toBeNull()
  })
})

// The topbar is a sticky stacking context at --z-topbar (200), so a panel
// rendered inside it is capped at 200 against root-context overlays no matter
// what z-index it declares. Teleporting to body is the only way out; fixed
// positioning keeps it anchored to the trigger from there.
describe('OrgProjectSwitcher panel escapes the topbar stacking context', () => {
  it('teleports the panel to body and positions it fixed', () => {
    const source = readComponentSource('app/components/OrgProjectSwitcher.vue')
    expect(source).toContain('<Teleport to="body">')

    const rule = topLevelRule(
      readComponentStyles('app/components/OrgProjectSwitcher.vue'),
      '.switcher__panel',
    )
    expect(rule).not.toBeNull()
    expect(declaration(rule as string, 'position')).toBe('fixed')
    expect(declaration(rule as string, 'z-index')).toBe('var(--z-dropdown)')
  })
})
