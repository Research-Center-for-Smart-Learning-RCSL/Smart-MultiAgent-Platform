import { beforeAll, describe, expect, it } from 'vitest'

import { renderView } from '../../../../tests/utils'
import SkillWorkbench from '../components/SkillWorkbench.vue'
import { installMessages, seedEmptyList, settle } from './kit'

// The right-hand pane's "nothing selected" arm is independent of the list, so
// the list is seeded empty rather than with fixtures this test does not read.
async function renderWorkbench() {
  seedEmptyList('/projects/p_1')
  const wrapper = await renderView(SkillWorkbench, {
    props: { scope: { kind: 'project', id: 'p_1' } },
  })
  await settle()
  return wrapper
}

describe('SkillWorkbench empty pane', () => {
  beforeAll(installMessages)

  // F-34: the call site passed :description, which SEmptyState does not
  // declare. Vue's default inheritAttrs turned it into a DOM attribute on the
  // root div, silently - vue-tsc does not reject it either, because no
  // vueCompilerOptions.strictTemplates is configured anywhere in frontend/.
  // So the guidance line never rendered, and no :icon meant no halo.
  it('renders the guidance body when no skill is selected', async () => {
    const wrapper = await renderWorkbench()
    expect(wrapper.text()).toContain('Pick a skill from the list, or create one.')
  })

  it('leaves no stray description attribute in the DOM', async () => {
    const wrapper = await renderWorkbench()
    expect(wrapper.findAll('[description]')).toHaveLength(0)
  })

  it('renders the icon halo like every other empty state', async () => {
    const wrapper = await renderWorkbench()
    expect(wrapper.find('.s-empty-state__halo').exists()).toBe(true)
  })
})
