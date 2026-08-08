// AC-1 (plugin path), AC-2 (form-renderer path), AC-5 (client score ignored),
// AC-7 (bridge selection). The api-client is mocked; the plugin captures its ctx.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import ActivityHost from '../components/ActivityHost.vue'
import { clearActivityPlugins, registerActivityPlugin } from '../plugins/registry'
import { MANDALA_9GRID_KEY, registerBundledPlugins } from '../plugins'
import { defineActivityPlugin } from '../sdk/defineActivityPlugin'
import type { ActivityRenderCtx } from '../sdk/types'
import type { ActivitySubmission, ActivityType } from '../types'

const submitActivityMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({ submitActivity: submitActivityMock }))

function activityType(over: Partial<ActivityType> = {}): ActivityType {
  return {
    id: 'at_1',
    key: 'demo',
    name: 'Demo',
    project_id: 'p1',
    payload_schema: {
      type: 'object',
      required: ['answer'],
      properties: { answer: { type: 'string', title: 'Answer' } },
    },
    validator_config: {},
    validator_kind: 'in_process',
    retention_days: null,
    created_at: null,
    ...over,
  } as ActivityType
}

function submissionOut(over: Partial<ActivitySubmission> = {}): ActivitySubmission {
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
    ...over,
  } as ActivitySubmission
}

afterEach(() => {
  clearActivityPlugins()
  submitActivityMock.mockReset()
})

describe('ActivityHost — form-renderer path (AC-2)', () => {
  it('renders the schema form when no plugin is registered and submits the object', async () => {
    submitActivityMock.mockResolvedValue(submissionOut())
    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })

    expect(wrapper.find('form').exists()).toBe(true)
    await wrapper.find('input').setValue('hello')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(submitActivityMock).toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({ activity_type_id: 'at_1', payload: { answer: 'hello' } }),
    )
    expect(wrapper.text()).toContain('activities.outcome.valid')
  })

  it('surfaces a submit failure inline without a floating rejection', async () => {
    submitActivityMock.mockRejectedValue(new Error('boom'))
    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })
    await wrapper.find('input').setValue('hello')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').text()).toContain('activities.host.submitFailed')
  })
})

describe('ActivityHost — plugin path (AC-1)', () => {
  it('mounts the plugin, routes emit through the api-client, and renders the server outcome', async () => {
    let ctx: ActivityRenderCtx | null = null
    registerActivityPlugin(
      defineActivityPlugin({
        manifest: { key: 'demo', version: '1', title: 'Demo' },
        render: (el, c) => {
          ctx = c
          el.appendChild(document.createElement('canvas'))
        },
      }),
    )
    submitActivityMock.mockResolvedValue(submissionOut())

    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })
    // The plugin rendered into the host-owned node, not the schema form.
    expect(wrapper.find('canvas').exists()).toBe(true)
    expect(wrapper.find('form').exists()).toBe(false)

    const result = await ctx!.emit({ answer: 'x' })
    await flushPromises()

    expect(result.status).toBe('validated')
    expect(wrapper.text()).toContain('activities.outcome.valid')
  })
})

describe('ActivityHost — server-side scoring authority (AC-5)', () => {
  it('ignores a client-supplied score; the outcome reflects only the backend result', async () => {
    let ctx: ActivityRenderCtx | null = null
    registerActivityPlugin(
      defineActivityPlugin({
        manifest: { key: 'demo', version: '1', title: 'Demo' },
        render: (_el, c) => {
          ctx = c
        },
      }),
    )
    // Backend says invalid regardless of the inflated client score.
    submitActivityMock.mockResolvedValue(
      submissionOut({ is_valid: false, validation_status: 'validated', sub_scores: { real: 0.1 } }),
    )

    const wrapper = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType() },
    })
    const result = await ctx!.emit({ score: 999, is_valid: true })
    await flushPromises()

    // The client payload was forwarded verbatim...
    expect(submitActivityMock).toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({ payload: { score: 999, is_valid: true } }),
    )
    // ...but the rendered outcome is the backend's, not the client's.
    expect(result.isValid).toBe(false)
    expect(wrapper.text()).toContain('activities.outcome.invalid')
    expect(wrapper.text()).not.toContain('activities.outcome.valid')
  })
})

describe('ActivityHost — bridge selection (AC-7)', () => {
  it('mounts the plugin through the injected HostBridge', async () => {
    const mount = vi.fn(() => () => {})
    registerActivityPlugin(
      defineActivityPlugin({
        manifest: { key: 'demo', version: '1', title: 'Demo' },
        render: () => {},
      }),
    )

    await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType(), bridge: { mount } },
    })

    expect(mount).toHaveBeenCalledTimes(1)
    const options = mount.mock.calls[0]![1] as { submit: unknown; session: { activityTypeKey: string } }
    expect(typeof options.submit).toBe('function')
    expect(options.session.activityTypeKey).toBe('demo')
  })

  it('routes the mandala-9grid key to the bundled plugin, other keys to the form (AC-7)', async () => {
    // The module-scope registration is cleared by afterEach and never re-runs
    // (ES module cache), so restore it through the exported idempotent helper.
    registerBundledPlugins()

    const gridSchema: Record<string, unknown> = { center: { type: 'string' } }
    for (let i = 1; i <= 8; i += 1) gridSchema[`cell_${i}`] = { type: 'string' }

    const withPlugin = await renderView(ActivityHost, {
      props: {
        chatroomId: 'c1',
        activityType: activityType({
          key: MANDALA_9GRID_KEY,
          payload_schema: { type: 'object', properties: gridSchema, required: ['center'] },
        }),
      },
    })
    expect(withPlugin.find('[data-testid="mandala-grid"]').exists()).toBe(true)
    expect(withPlugin.find('form').exists()).toBe(false)

    const withoutPlugin = await renderView(ActivityHost, {
      props: { chatroomId: 'c1', activityType: activityType({ key: 'six-hats-emotion-desk' }) },
    })
    expect(withoutPlugin.find('[data-testid="mandala-grid"]').exists()).toBe(false)
    expect(withoutPlugin.find('form').exists()).toBe(true)
  })
})
