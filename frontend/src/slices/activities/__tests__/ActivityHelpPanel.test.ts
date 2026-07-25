// Agent-visibility follow-up: the Activities feature guide drawer. The test
// i18n harness echoes keys, so assertions target the exact key rather than
// translated copy.

import { describe, expect, it } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ActivityHelpPanel from '../components/ActivityHelpPanel.vue'

const SECTIONS = ['types', 'schema', 'validators', 'activation', 'plugins', 'visibility', 'retention']

describe('ActivityHelpPanel', () => {
  it('renders nothing when closed', async () => {
    const wrapper = await renderView(ActivityHelpPanel, { props: { open: false } })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it('renders the title, intro, and every section when open', async () => {
    const wrapper = await renderView(ActivityHelpPanel, { props: { open: true } })

    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('activities.helpPanel.title')
    expect(wrapper.text()).toContain('activities.helpPanel.intro')
    for (const section of SECTIONS) {
      expect(wrapper.text()).toContain(`activities.helpPanel.${section}.title`)
      expect(wrapper.text()).toContain(`activities.helpPanel.${section}.body`)
    }
  })

  it('emits close when the drawer requests it', async () => {
    const wrapper = await renderView(ActivityHelpPanel, { props: { open: true } })

    await wrapper.find('.s-drawer__close').trigger('click')

    expect(wrapper.emitted('close')).toBeTruthy()
  })
})
