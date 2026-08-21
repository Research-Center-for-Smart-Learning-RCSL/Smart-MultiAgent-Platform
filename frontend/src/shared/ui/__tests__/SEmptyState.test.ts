import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import { declaration, readComponentStyles, topLevelRule } from '../../../../tests/utils'
import SEmptyState from '../SEmptyState.vue'

const DECLARED_PROPS = ['title', 'text', 'icon']

describe('SEmptyState', () => {
  // F-30: the rule specified cross-axis centring and horizontal block centring
  // but no main-axis distribution, so a stretched instance packed its content
  // to the top - GraphragGraphView's two flex-1 empty states sat under the
  // summary line with ~600px of blank beneath. Structural guard only: jsdom
  // applies no scoped CSS and performs no layout.
  it('centres its content on the main axis when a consumer stretches it', () => {
    const rule = topLevelRule(
      readComponentStyles('shared/ui/SEmptyState.vue'),
      '.s-empty-state',
    )
    expect(rule).not.toBeNull()
    expect(declaration(rule as string, 'justify-content')).toBe('center')
    // A no-op at content height, so it cannot disturb the unstretched majority.
    expect(declaration(rule as string, 'flex-direction')).toBe('column')
  })

  // `description` is NOT one of them, which is why SkillWorkbench's :description
  // fell through to the root div as a stray attribute instead of failing. This
  // pins the API so a call site cannot invent a second name for `text`;
  // fallthrough itself stays on, because consumers legitimately pass `class`
  // (GraphragGraphView's flex-1 is what the centring rule above exists for).
  it('declares exactly the props its call sites may pass', () => {
    const wrapper = mount(SEmptyState, { props: { title: 'Nothing here' } })
    expect(Object.keys(wrapper.vm.$props).sort()).toEqual([...DECLARED_PROPS].sort())
  })
})
