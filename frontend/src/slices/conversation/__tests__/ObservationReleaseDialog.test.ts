import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ObservationReleaseDialog from '../components/ObservationReleaseDialog.vue'
import type { Observation } from '../types'

function observation(): Observation {
  return {
    id: 'o1',
    chatroom_id: 'c1',
    agent_id: 'a1',
    content_md: 'draft analysis',
    metadata: {},
    trigger: 'every_n_messages',
    trigger_message_id: null,
    released_at: null,
    release_target: null,
    released_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

const normalAgents = [
  { id: 'n1', name: 'Helper-1' },
  { id: 'n2', name: 'Helper-2' },
]

describe('ObservationReleaseDialog', () => {
  it('emits a room-target release with an edited content override', async () => {
    const wrapper = await renderView(ObservationReleaseDialog, {
      props: { open: true, observation: observation(), disclose: false, normalAgents },
    })

    const textarea = wrapper.find('textarea')
    await textarea.setValue('edited analysis')

    // The last footer button is the confirm ("Release").
    const buttons = wrapper.findAll('button')
    await buttons[buttons.length - 1].trigger('click')

    const emitted = wrapper.emitted('submit')
    expect(emitted).toBeTruthy()
    expect(emitted![0][0]).toEqual({ target: 'room', content_override: 'edited analysis' })
  })

  it('offers only the normal-role agents when the private target is chosen', async () => {
    const wrapper = await renderView(ObservationReleaseDialog, {
      props: { open: true, observation: observation(), disclose: true, normalAgents },
    })
    // Pick the "agents" radio (second radio input).
    const radios = wrapper.findAll('input[type="radio"]')
    await radios[1].setValue()

    expect(wrapper.text()).toContain('Helper-1')
    expect(wrapper.text()).toContain('Helper-2')
  })
})
