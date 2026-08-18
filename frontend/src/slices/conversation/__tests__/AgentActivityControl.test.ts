// The delegation control in the chatroom settings ([R30.37]).
//
// The test i18n harness echoes keys (bundles load lazily), so assert on structure
// and the exact key rather than translated copy.

import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import AgentActivityControl from '../components/AgentActivityControl.vue'
import type { ActivityType } from '@slices/activities'
import type { BoundAgent } from '../composables/useChatroomBindings'

function activityType(over: Partial<ActivityType> = {}): ActivityType {
  return {
    id: 'at_1',
    project_id: 'p1',
    scope: 'project',
    key: 'unit2',
    name: 'Unit 2',
    payload_schema: {},
    validator_kind: 'in_process',
    validator_config: {},
    retention_days: null,
    expose_payload_to_agent: true,
    echo_includes_content: false,
    created_at: null,
    ...over,
  } as ActivityType
}

function boundAgent(over: Partial<BoundAgent> = {}): BoundAgent {
  return {
    id: 'ag_1',
    name: 'TA',
    wakeup_config: { triggers: {} } as BoundAgent['wakeup_config'],
    role: 'normal',
    may_control_activities: false,
    activity_type_allowlist: [],
    ...over,
  }
}

function applyButton(wrapper: { findAll: (s: string) => Array<{ text: () => string }> }) {
  return wrapper
    .findAll('button')
    .find((b) => b.text().includes('conversation.activityControl.apply'))
}

describe('AgentActivityControl', () => {
  it('offers no allowlist until the grant is switched on', async () => {
    const wrapper = await renderView(AgentActivityControl, {
      props: { agent: boundAgent(), activityTypes: [activityType()], activityTypesFailed: false, busy: false },
    })

    expect(wrapper.text()).toContain('conversation.activityControl.label')
    expect(wrapper.text()).not.toContain('conversation.activityControl.allowlist')
  })

  it('seeds the draft from the stored grant', async () => {
    const wrapper = await renderView(AgentActivityControl, {
      props: {
        agent: boundAgent({ may_control_activities: true, activity_type_allowlist: ['at_1'] }),
        activityTypes: [activityType()],
        activityTypesFailed: false,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('conversation.activityControl.allowlist')
    expect(wrapper.text()).toContain('Unit 2')
    // Nothing has been changed, so there is nothing to apply — the button must not
    // invite a write that would be a no-op.
    expect(applyButton(wrapper)?.attributes('disabled')).toBeDefined()
  })

  it('emits the whole decision at once when applied', async () => {
    // Toggle and checkboxes are two halves of one state the server accepts or
    // refuses together, so they are drafted locally and sent together.
    const wrapper = await renderView(AgentActivityControl, {
      props: { agent: boundAgent(), activityTypes: [activityType()], activityTypesFailed: false, busy: false },
    })

    await wrapper.find('button[role="switch"]').trigger('click')
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await applyButton(wrapper)!.trigger('click')

    expect(wrapper.emitted('save')).toEqual([[true, ['at_1']]])
  })

  it('states the observer asymmetry only while the grant is on', async () => {
    // Q-6: permitted, and named at the moment of granting — an observer is silent
    // to the class ([R28.02]) while starting a round is not.
    const observer = boundAgent({ role: 'observer' })
    const off = await renderView(AgentActivityControl, {
      props: { agent: observer, activityTypes: [activityType()], activityTypesFailed: false, busy: false },
    })
    expect(off.text()).not.toContain('conversation.activityControl.observerNote')

    const on = await renderView(AgentActivityControl, {
      props: {
        agent: boundAgent({ role: 'observer', may_control_activities: true, activity_type_allowlist: ['at_1'] }),
        activityTypes: [activityType()],
        activityTypesFailed: false,
        busy: false,
      },
    })
    expect(on.text()).toContain('conversation.activityControl.observerNote')
  })

  it('reports a stored allowlist entry the project can no longer use', async () => {
    // The server drops these at turn-assembly time, so without this the agent
    // quietly runs fewer activities than the teacher picked.
    const wrapper = await renderView(AgentActivityControl, {
      props: {
        agent: boundAgent({
          may_control_activities: true,
          activity_type_allowlist: ['at_1', 'at_gone'],
        }),
        activityTypes: [activityType()],
        activityTypesFailed: false,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('conversation.activityControl.unresolved')
  })

  it('drops an unusable entry from the draft so applying repairs the row', async () => {
    // An unusable id has no checkbox, so keeping it in the draft would make it
    // unremovable — and the grant route validates every id it is sent, so every
    // later Apply would 422 with nothing on screen saying which entry did it.
    const wrapper = await renderView(AgentActivityControl, {
      props: {
        agent: boundAgent({
          may_control_activities: true,
          activity_type_allowlist: ['at_1', 'at_gone'],
        }),
        activityTypes: [activityType()],
        activityTypesFailed: false,
        busy: false,
      },
    })

    // Enabled on load precisely because the stored row still carries the dead id.
    expect(applyButton(wrapper)?.attributes('disabled')).toBeUndefined()
    await applyButton(wrapper)!.trigger('click')

    expect(wrapper.emitted('save')).toEqual([[true, ['at_1']]])
  })

  it('does not offer to apply a leftover on an ungranted binding', async () => {
    // A revoke writes no allowlist, so the stale entry cannot be repaired while
    // the grant is off — an enabled Apply here would be a button that lies.
    const wrapper = await renderView(AgentActivityControl, {
      props: {
        agent: boundAgent({
          may_control_activities: false,
          activity_type_allowlist: ['at_1', 'at_gone'],
        }),
        activityTypes: [activityType()],
        activityTypesFailed: false,
        busy: false,
      },
    })

    expect(applyButton(wrapper)?.attributes('disabled')).toBeDefined()
  })

  it('tells a failed type listing apart from a project that has none', async () => {
    // "Register one before delegating control" is an instruction; giving it when
    // the listing merely failed sends the teacher to create what already exists.
    const wrapper = await renderView(AgentActivityControl, {
      props: {
        agent: boundAgent({ may_control_activities: true, activity_type_allowlist: ['at_1'] }),
        activityTypes: [],
        activityTypesFailed: true,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('conversation.activityControl.typesLoadFailed')
    expect(wrapper.text()).not.toContain('conversation.activityControl.noTypes')
  })

  it('claims nothing about the selection when the listing failed', async () => {
    // An empty `activityTypes` from a *failure* makes every stored entry look
    // unresolved, so an unguarded count would tell the teacher their whole
    // selection had been deleted — off one network hiccup.
    const wrapper = await renderView(AgentActivityControl, {
      props: {
        agent: boundAgent({
          may_control_activities: true,
          activity_type_allowlist: ['at_1', 'at_2'],
        }),
        activityTypes: [],
        activityTypesFailed: true,
        busy: false,
      },
    })

    expect(wrapper.text()).not.toContain('conversation.activityControl.unresolved')
  })

  it('does not offer to apply anything when the listing failed', async () => {
    // The draft narrows to nothing, so Apply would emit save(true, []) and be
    // refused by the client guard for want of a selection never offered.
    const wrapper = await renderView(AgentActivityControl, {
      props: {
        agent: boundAgent({ may_control_activities: true, activity_type_allowlist: ['at_1'] }),
        activityTypes: [],
        activityTypesFailed: true,
        busy: false,
      },
    })

    expect(applyButton(wrapper)?.attributes('disabled')).toBeDefined()
    // And flipping the switch does not make an unanswerable form answerable.
    await wrapper.find('button[role="switch"]').trigger('click')
    expect(applyButton(wrapper)?.attributes('disabled')).toBeDefined()
    expect(wrapper.emitted('save')).toBeUndefined()
  })

  it('does not offer to apply a grant with nothing ticked', async () => {
    const wrapper = await renderView(AgentActivityControl, {
      props: {
        agent: boundAgent({ may_control_activities: true, activity_type_allowlist: ['at_1'] }),
        activityTypes: [activityType()],
        activityTypesFailed: false,
        busy: false,
      },
    })

    await wrapper.find('input[type="checkbox"]').setValue(false)

    expect(applyButton(wrapper)?.attributes('disabled')).toBeDefined()
  })

  it('says so when the project has no activity types to delegate', async () => {
    const wrapper = await renderView(AgentActivityControl, {
      props: {
        agent: boundAgent({ may_control_activities: true, activity_type_allowlist: ['at_1'] }),
        activityTypes: [],
        activityTypesFailed: false,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('conversation.activityControl.noTypes')
    expect(wrapper.text()).not.toContain('conversation.activityControl.allowlist')
  })

  it('disables everything while a write is in flight', async () => {
    const wrapper = await renderView(AgentActivityControl, {
      props: { agent: boundAgent(), activityTypes: [activityType()], activityTypesFailed: false, busy: true },
    })

    expect(wrapper.find('button[role="switch"]').attributes('disabled')).toBeDefined()
    expect(applyButton(wrapper)?.attributes('disabled')).toBeDefined()
  })
})
