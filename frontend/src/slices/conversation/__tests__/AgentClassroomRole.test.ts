// The classroom role selector ([R34.01]). The test i18n harness echoes keys.

import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import AgentClassroomRole from '../components/AgentClassroomRole.vue'
import type { BoundAgent } from '../composables/useChatroomBindings'

function boundAgent(over: Partial<BoundAgent> = {}): BoundAgent {
  return { id: 'ag_1', name: 'TA', role: 'normal', classroom_role: 'none', ...over }
}

function optionValues(wrapper: { findAll: (s: string) => Array<{ attributes: (n: string) => string | undefined }> }) {
  return wrapper.findAll('option').map((o) => o.attributes('value'))
}

describe('AgentClassroomRole', () => {
  it('offers teacher and peer on a participant binding, never analyst', async () => {
    const wrapper = await renderView(AgentClassroomRole, { props: { agent: boundAgent(), busy: false, roomHasTeacher: false } })
    expect(optionValues(wrapper)).toEqual(['none', 'teacher', 'peer'])
  })

  it('offers only analyst on an observer binding', async () => {
    const wrapper = await renderView(AgentClassroomRole, {
      props: { agent: boundAgent({ role: 'observer' }), busy: false, roomHasTeacher: false },
    })
    expect(optionValues(wrapper)).toEqual(['none', 'analyst'])
  })

  it('shows the stored role and emits the chosen one', async () => {
    const wrapper = await renderView(AgentClassroomRole, {
      props: { agent: boundAgent({ classroom_role: 'teacher' }), busy: false, roomHasTeacher: true },
    })
    const select = wrapper.find('select')
    expect((select.element as HTMLSelectElement).value).toBe('teacher')
    await select.setValue('peer')
    expect(wrapper.emitted('save')).toEqual([['peer']])
  })

  it('is disabled while a binding write is in flight', async () => {
    const wrapper = await renderView(AgentClassroomRole, { props: { agent: boundAgent(), busy: true, roomHasTeacher: false } })
    expect(wrapper.find('select').attributes('disabled')).toBeDefined()
  })

  // Q-9: without a teacher agent every peer reply expires unposted.
  it('warns on a peer binding only while the room has no teacher agent', async () => {
    const lone = await renderView(AgentClassroomRole, {
      props: { agent: boundAgent({ classroom_role: 'peer' }), busy: false, roomHasTeacher: false },
    })
    expect(lone.find('[data-testid="classroom-role-no-teacher"]').text()).toBe('conversation.classroomRole.peerWithoutTeacher')

    const taught = await renderView(AgentClassroomRole, {
      props: { agent: boundAgent({ classroom_role: 'peer' }), busy: false, roomHasTeacher: true },
    })
    expect(taught.find('[data-testid="classroom-role-no-teacher"]').exists()).toBe(false)

    const notPeer = await renderView(AgentClassroomRole, {
      props: { agent: boundAgent({ classroom_role: 'none' }), busy: false, roomHasTeacher: false },
    })
    expect(notPeer.find('[data-testid="classroom-role-no-teacher"]').exists()).toBe(false)
  })
})
