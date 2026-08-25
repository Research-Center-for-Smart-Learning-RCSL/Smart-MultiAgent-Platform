// AC-4 / AC-13 — the worksheet's draft reporting, and its retraction (§32).
//
// Three properties, and the second is the one whose absence would be invisible:
//
// 1. Typing into a worksheet emits `draft` UPWARD, on a throttle, and never sends
//    anything itself. The activities slice must not touch the chatroom socket
//    (gate #1's SLICE_DEPS), so the host emits and `ChatroomView` sends.
// 2. A successful submit and an unmount both emit `draftClear`, and both CANCEL a
//    pending throttled report first. Without the cancel, the timer fires after the
//    clear and re-reports an answer that has already been submitted — leaving a
//    "draft" of a sent worksheet readable for a full TTL.
// 3. A FAILED submit does not clear: nothing was sent, so the text is still unsent.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import ActivityHost from '../components/ActivityHost.vue'
import { clearActivityPlugins } from '../plugins'
import type { ActivitySubmission, ActivityType } from '../types'

const submitActivityMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({ submitActivity: submitActivityMock }))

const THROTTLE_MS = 3000

function activityType(): ActivityType {
  return {
    id: 'at_1',
    // A key with no bundled plugin, so the schema-form path renders. The plugin
    // path's own reporting goes through `ctx.draft`, which `sdk.test.ts` covers;
    // this file is about the form path and the throttle both paths share.
    key: 'demo',
    name: 'Demo',
    project_id: 'p1',
    payload_schema: {
      type: 'object',
      properties: { answer: { type: 'string', title: 'Answer' } },
    },
    validator_config: {},
    validator_kind: 'in_process',
    retention_days: null,
    created_at: null,
  } as ActivityType
}

function submissionOut(): ActivitySubmission {
  return {
    id: 'sub_1',
    activity_type_id: 'at_1',
    chatroom_id: 'c1',
    session_id: 's1',
    attempt_no: 1,
    validation_status: 'validated',
    is_valid: true,
    sub_scores: {},
    error_class: null,
    latency_ms: 5,
    created_at: null,
  } as ActivitySubmission
}

beforeEach(() => {
  vi.useFakeTimers()
  // Not only in afterEach: the registry is process-global, so the FIRST test in
  // this file would otherwise inherit whatever another module registered at
  // import time and render the plugin path instead of the form.
  clearActivityPlugins()
})

afterEach(() => {
  vi.useRealTimers()
  clearActivityPlugins()
  submitActivityMock.mockReset()
})

describe('ActivityHost — reporting the unsent worksheet (AC-4)', () => {
  it('emits draft upward on the throttle, not per keystroke', async () => {
    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })

    await wrapper.find('input').setValue('a')
    await wrapper.find('input').setValue('ab')
    await wrapper.find('input').setValue('abc')

    // Nothing yet: the window has not closed.
    expect(wrapper.emitted('draft')).toBeUndefined()

    vi.advanceTimersByTime(THROTTLE_MS)
    await flushPromises()

    // One report for the whole burst, carrying the LATEST state. A leading-edge
    // throttle would have reported "a" and then nothing.
    const emitted = wrapper.emitted('draft')
    expect(emitted).toHaveLength(1)
    expect(emitted![0]![0]).toMatchObject({ answer: 'abc' })
  })

  it('reports the raw field values, including the ones still being written', async () => {
    // Deliberately NOT `assemblePayload`'s output: a half-typed field is exactly
    // what a draft is, and the assembler drops the incomplete ones — which are the
    // ones worth seeing.
    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })

    await wrapper.find('input').setValue('half a th')
    vi.advanceTimersByTime(THROTTLE_MS)
    await flushPromises()

    expect(wrapper.emitted('draft')![0]![0]).toMatchObject({ answer: 'half a th' })
  })

  it('does not report an untouched form', async () => {
    // The initial values are the empty worksheet. Reporting them would write an
    // entry the participant has not touched, which an agent reads as "started".
    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })

    vi.advanceTimersByTime(THROTTLE_MS * 2)
    await flushPromises()

    expect(wrapper.emitted('draft')).toBeUndefined()
  })

  it('sends nothing itself — the report only ever goes upward', async () => {
    // Gate #1: `activities` must never import `conversation`, so the host cannot
    // reach the socket even if someone wanted it to. This asserts the observable
    // half: the component's only outward action is a Vue emit.
    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })

    await wrapper.find('input').setValue('x')
    vi.advanceTimersByTime(THROTTLE_MS)
    await flushPromises()

    expect(submitActivityMock).not.toHaveBeenCalled()
    expect(wrapper.emitted('draft')).toHaveLength(1)
  })
})

describe('ActivityHost — retracting the draft (AC-4)', () => {
  it('clears on a successful submit', async () => {
    submitActivityMock.mockResolvedValue(submissionOut())
    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })

    await wrapper.find('input').setValue('an answer')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.emitted('draftClear')).toHaveLength(1)
  })

  it('cancels a pending report so a submitted answer is never re-reported', async () => {
    // The regression this whole file exists to prevent. Without the cancel the
    // timer fires after the clear, re-reporting text that is now a submission —
    // and a submission is governed by its own consent rules, not this one.
    submitActivityMock.mockResolvedValue(submissionOut())
    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })

    await wrapper.find('input').setValue('an answer')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    vi.advanceTimersByTime(THROTTLE_MS * 2)
    await flushPromises()

    expect(wrapper.emitted('draftClear')).toHaveLength(1)
    expect(wrapper.emitted('draft')).toBeUndefined()
  })

  it('does NOT clear when the submit failed', async () => {
    // Nothing was sent, so the text is still unsent. Clearing here would hide a
    // live draft from the one agent the room granted, which is a smaller harm than
    // the reverse but still the wrong answer.
    submitActivityMock.mockRejectedValue(new Error('server said no'))
    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })

    await wrapper.find('input').setValue('an answer')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.emitted('draftClear')).toBeUndefined()
  })

  it('clears on unmount, and leaves no timer behind', async () => {
    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })
    await wrapper.find('input').setValue('left on the tab')

    wrapper.unmount()
    vi.advanceTimersByTime(THROTTLE_MS * 2)

    expect(wrapper.emitted('draftClear')).toHaveLength(1)
    // A timer surviving unmount would emit into a component that is gone; with
    // fake timers that is silent, which is exactly why it needs asserting.
    expect(wrapper.emitted('draft')).toBeUndefined()
  })
})
