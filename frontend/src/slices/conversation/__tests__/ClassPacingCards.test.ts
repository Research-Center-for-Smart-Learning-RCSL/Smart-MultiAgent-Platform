// Class start and per-type time limits in room settings (phase 4 AC-8).

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useConfirmDialog } from '@shared/composables'
import type { ActivityType } from '@slices/activities'
import ClassStartCard from '../components/ClassStartCard.vue'
import ActivityTimeLimitsCard from '../components/ActivityTimeLimitsCard.vue'

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('vue-sonner', () => ({ toast }))

beforeEach(() => {
  toast.success.mockReset()
  toast.error.mockReset()
})

function classStart(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    configured: true,
    enabled: true,
    min_participants: 4,
    max_wait_minutes: 10,
    first_arrival_at: null,
    started_at: null,
    ...overrides,
  }
}

async function mountClassStart() {
  const wrapper = await renderView(ClassStartCard, { props: { chatroomId: 'cr_1' } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('ClassStartCard', () => {
  it('shows a loading state until the settings arrive', async () => {
    server.use(http.get('/api/chatrooms/:id/class-start', () => new Promise<Response>(() => {})))
    const wrapper = await renderView(ClassStartCard, { props: { chatroomId: 'cr_1' } })
    await flushPromises()
    expect(wrapper.find('.s-skeleton').exists()).toBe(true)
    expect(wrapper.find('[data-testid="class-start-save"]').exists()).toBe(false)
  })

  it('shows a retryable error when the read fails', async () => {
    let reads = 0
    server.use(
      http.get('/api/chatrooms/:id/class-start', () => {
        reads += 1
        return reads === 1 ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(classStart())
      }),
    )
    const wrapper = await mountClassStart()
    expect(wrapper.text()).toContain('conversation.classStart.loadFailed')

    await wrapper.find('button').trigger('click')
    await flushPromises()
    await flushPromises()
    expect(reads).toBe(2)
    expect(wrapper.find('[data-testid="class-start-save"]').exists()).toBe(true)
  })

  it('hides itself when the caller is not the room creator', async () => {
    server.use(
      http.get('/api/chatrooms/:id/class-start', () =>
        HttpResponse.json(
          { type: 'about:blank', title: 'Forbidden', status: 403, code: 'conversation/not-room-creator' },
          { status: 403, headers: { 'content-type': 'application/problem+json' } },
        ),
      ),
    )
    const wrapper = await mountClassStart()
    expect(wrapper.find('[data-testid="class-start-card"]').exists()).toBe(false)
  })

  it('sends all three settings on the first save of an unconfigured room', async () => {
    const writes: unknown[] = []
    server.use(
      http.get('/api/chatrooms/:id/class-start', () =>
        HttpResponse.json(
          classStart({ configured: false, enabled: false, min_participants: null, max_wait_minutes: null }),
        ),
      ),
      http.patch('/api/chatrooms/:id/class-start', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>
        writes.push(body)
        return HttpResponse.json(classStart({ ...body, configured: true }))
      }),
    )
    const wrapper = await mountClassStart()
    expect(wrapper.find('[data-testid="class-start-state"]').text()).toBe(
      'conversation.classStart.state.unconfigured',
    )

    await wrapper.find('[data-testid="class-start-enabled"] button').trigger('click')
    await wrapper.find('[data-testid="class-start-min"]').setValue('6')
    await wrapper.find('[data-testid="class-start-wait"]').setValue('8')
    await wrapper.find('[data-testid="class-start-save"]').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(writes).toEqual([{ enabled: true, min_participants: 6, max_wait_minutes: 8 }])
    expect(toast.success).toHaveBeenCalledWith('conversation.classStart.saved', expect.anything())
    expect(wrapper.find('[data-testid="class-start-save"]').attributes('disabled')).toBeDefined()
  })

  it('refuses an out-of-range value before it reaches the server', async () => {
    const writes: unknown[] = []
    server.use(
      http.get('/api/chatrooms/:id/class-start', () => HttpResponse.json(classStart())),
      http.patch('/api/chatrooms/:id/class-start', async ({ request }) => {
        writes.push(await request.json())
        return HttpResponse.json(classStart())
      }),
    )
    const wrapper = await mountClassStart()
    await wrapper.find('[data-testid="class-start-wait"]').setValue('61')
    expect(wrapper.text()).toContain('conversation.classStart.invalid')
    expect(wrapper.find('[data-testid="class-start-save"]').attributes('disabled')).toBeDefined()
    await wrapper.find('[data-testid="class-start-save"]').trigger('click')
    await flushPromises()
    expect(writes).toEqual([])
  })

  it('reports a failed save', async () => {
    server.use(
      http.get('/api/chatrooms/:id/class-start', () => HttpResponse.json(classStart())),
      http.patch('/api/chatrooms/:id/class-start', () => new HttpResponse(null, { status: 500 })),
    )
    const wrapper = await mountClassStart()
    await wrapper.find('[data-testid="class-start-min"]').setValue('5')
    await wrapper.find('[data-testid="class-start-save"]').trigger('click')
    await flushPromises()
    await flushPromises()
    expect(toast.error).toHaveBeenCalledWith('conversation.classStart.saveFailed', expect.anything())
  })

  it('shows the waiting state and leaves reset off before anyone arrives', async () => {
    server.use(http.get('/api/chatrooms/:id/class-start', () => HttpResponse.json(classStart())))
    const idle = await mountClassStart()
    expect(idle.find('[data-testid="class-start-state"]').text()).toBe('conversation.classStart.state.idle')
    expect(idle.find('[data-testid="class-start-reset"]').attributes('disabled')).toBeDefined()

    server.use(
      http.get('/api/chatrooms/:id/class-start', () =>
        HttpResponse.json(classStart({ first_arrival_at: '2026-10-27T09:00:00Z' })),
      ),
    )
    const waiting = await mountClassStart()
    expect(waiting.find('[data-testid="class-start-state"]').text()).toBe(
      'conversation.classStart.state.waiting',
    )
  })

  it('resets a started class after confirmation', async () => {
    const writes: unknown[] = []
    server.use(
      http.get('/api/chatrooms/:id/class-start', () =>
        HttpResponse.json(
          classStart({ first_arrival_at: '2026-10-27T09:00:00Z', started_at: '2026-10-27T09:03:00Z' }),
        ),
      ),
      http.patch('/api/chatrooms/:id/class-start', async ({ request }) => {
        writes.push(await request.json())
        return HttpResponse.json(classStart())
      }),
    )
    const wrapper = await mountClassStart()
    expect(wrapper.find('[data-testid="class-start-state"]').text()).toBe(
      'conversation.classStart.state.started',
    )

    await wrapper.find('[data-testid="class-start-reset"]').trigger('click')
    await flushPromises()
    useConfirmDialog().handleConfirm()
    await flushPromises()
    await flushPromises()

    expect(writes).toEqual([{ reset: true }])
    expect(toast.success).toHaveBeenCalledWith('conversation.classStart.resetDone', expect.anything())
    expect(wrapper.find('[data-testid="class-start-state"]').text()).toBe('conversation.classStart.state.idle')
  })
})

function makeType(id: string, name: string, defaultMinutes: number | null): ActivityType {
  return {
    id,
    name,
    key: id,
    scope: 'project',
    default_time_limit_minutes: defaultMinutes,
  } as ActivityType
}

const TYPES = [makeType('at_1', 'Quiz', 5), makeType('at_2', 'Poll', null), makeType('at_3', 'Essay', null)]

async function mountLimits(
  types: ActivityType[] = TYPES,
  extra: { activityTypesFailed?: boolean; activityTypesLoading?: boolean } = {},
) {
  const wrapper = await renderView(ActivityTimeLimitsCard, {
    props: {
      chatroomId: 'cr_1',
      activityTypes: types,
      activityTypesFailed: extra.activityTypesFailed ?? false,
      activityTypesLoading: extra.activityTypesLoading ?? false,
    },
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

function row(wrapper: Awaited<ReturnType<typeof mountLimits>>, id: string) {
  return wrapper.find(`[data-testid="time-limit-${id}"]`)
}

describe('ActivityTimeLimitsCard', () => {
  it('seeds each type from the stored overrides: default, none and custom', async () => {
    server.use(
      http.get('/api/chatrooms/:id/activity-time-limits', () =>
        HttpResponse.json({
          limits: [
            { activity_type_id: 'at_2', time_limit_minutes: null },
            { activity_type_id: 'at_3', time_limit_minutes: 12 },
          ],
        }),
      ),
    )
    const wrapper = await mountLimits()

    const quiz = row(wrapper, 'at_1').find('select').element as HTMLSelectElement
    expect(quiz.value).toBe('default')
    expect(row(wrapper, 'at_1').text()).toContain('conversation.timeLimits.useDefaultMinutes')
    expect(row(wrapper, 'at_1').find('[data-testid="time-limit-minutes"]').exists()).toBe(false)

    expect((row(wrapper, 'at_2').find('select').element as HTMLSelectElement).value).toBe('none')
    expect(row(wrapper, 'at_2').text()).toContain('conversation.timeLimits.useDefaultNone')

    expect((row(wrapper, 'at_3').find('select').element as HTMLSelectElement).value).toBe('custom')
    const minutes = row(wrapper, 'at_3').find('input').element as HTMLInputElement
    expect(minutes.value).toBe('12')
    expect(wrapper.find('[data-testid="time-limits-save"]').attributes('disabled')).toBeDefined()
  })

  it('puts the full list, leaving defaulted types out', async () => {
    const writes: unknown[] = []
    server.use(
      http.get('/api/chatrooms/:id/activity-time-limits', () =>
        HttpResponse.json({ limits: [{ activity_type_id: 'at_3', time_limit_minutes: 12 }] }),
      ),
      http.put('/api/chatrooms/:id/activity-time-limits', async ({ request }) => {
        const body = await request.json()
        writes.push(body)
        return HttpResponse.json(body)
      }),
    )
    const wrapper = await mountLimits()

    await row(wrapper, 'at_1').find('select').setValue('custom')
    // Switching to custom pre-fills the type's default as a starting point.
    expect((row(wrapper, 'at_1').find('input').element as HTMLInputElement).value).toBe('5')
    await row(wrapper, 'at_1').find('input').setValue('7')
    await row(wrapper, 'at_2').find('select').setValue('none')
    await row(wrapper, 'at_3').find('select').setValue('default')

    await wrapper.find('[data-testid="time-limits-save"]').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(writes).toEqual([
      {
        limits: [
          { activity_type_id: 'at_1', time_limit_minutes: 7 },
          { activity_type_id: 'at_2', time_limit_minutes: null },
        ],
      },
    ])
    expect(toast.success).toHaveBeenCalledWith('conversation.timeLimits.saved', expect.anything())
    expect(wrapper.find('[data-testid="time-limits-save"]').attributes('disabled')).toBeDefined()
  })

  it('drops a stored override for a type the room can no longer reach', async () => {
    const writes: unknown[] = []
    server.use(
      http.get('/api/chatrooms/:id/activity-time-limits', () =>
        HttpResponse.json({ limits: [{ activity_type_id: 'gone', time_limit_minutes: 9 }] }),
      ),
      http.put('/api/chatrooms/:id/activity-time-limits', async ({ request }) => {
        const body = await request.json()
        writes.push(body)
        return HttpResponse.json(body)
      }),
    )
    const wrapper = await mountLimits()
    // The stored row differs from what can be sent, so Save is offered and repairs it.
    await wrapper.find('[data-testid="time-limits-save"]').trigger('click')
    await flushPromises()
    expect(writes).toEqual([{ limits: [] }])
  })

  it('blocks a custom limit outside 1..180', async () => {
    server.use(http.get('/api/chatrooms/:id/activity-time-limits', () => HttpResponse.json({ limits: [] })))
    const wrapper = await mountLimits()
    await row(wrapper, 'at_2').find('select').setValue('custom')
    await row(wrapper, 'at_2').find('input').setValue('181')
    expect(wrapper.text()).toContain('conversation.timeLimits.invalid')
    expect(wrapper.find('[data-testid="time-limits-save"]').attributes('disabled')).toBeDefined()
  })

  it('reports a failed save', async () => {
    server.use(
      http.get('/api/chatrooms/:id/activity-time-limits', () => HttpResponse.json({ limits: [] })),
      http.put('/api/chatrooms/:id/activity-time-limits', () => new HttpResponse(null, { status: 500 })),
    )
    const wrapper = await mountLimits()
    await row(wrapper, 'at_2').find('select').setValue('none')
    await wrapper.find('[data-testid="time-limits-save"]').trigger('click')
    await flushPromises()
    await flushPromises()
    expect(toast.error).toHaveBeenCalledWith('conversation.timeLimits.saveFailed', expect.anything())
  })

  it('shows the empty state when no activity types are reachable', async () => {
    server.use(http.get('/api/chatrooms/:id/activity-time-limits', () => HttpResponse.json({ limits: [] })))
    const wrapper = await mountLimits([])
    expect(wrapper.find('[data-testid="time-limits-empty"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('conversation.timeLimits.noTypes')
    expect(wrapper.find('[data-testid="time-limits-save"]').exists()).toBe(false)
  })

  it('says the type listing failed rather than that there are none', async () => {
    server.use(http.get('/api/chatrooms/:id/activity-time-limits', () => HttpResponse.json({ limits: [] })))
    const wrapper = await mountLimits([], { activityTypesFailed: true })
    expect(wrapper.text()).toContain('conversation.timeLimits.typesLoadFailed')
    expect(wrapper.find('[data-testid="time-limits-empty"]').exists()).toBe(false)
  })

  it('shows a loading state while the types are still pending', async () => {
    server.use(http.get('/api/chatrooms/:id/activity-time-limits', () => HttpResponse.json({ limits: [] })))
    const wrapper = await mountLimits([], { activityTypesLoading: true })
    expect(wrapper.find('[data-testid="time-limits-empty"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="time-limits-save"]').exists()).toBe(false)
  })

  it('shows a retryable error when the overrides cannot be read', async () => {
    server.use(
      http.get('/api/chatrooms/:id/activity-time-limits', () => new HttpResponse(null, { status: 500 })),
    )
    const wrapper = await mountLimits()
    expect(wrapper.text()).toContain('conversation.timeLimits.loadFailed')
    expect(wrapper.find('[data-testid="time-limits-save"]').exists()).toBe(false)
  })
})
