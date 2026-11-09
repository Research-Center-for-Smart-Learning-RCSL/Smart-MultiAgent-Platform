// "Design classroom agents" panel ([R34.08], AC-19). The test i18n harness echoes keys.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import ClassroomDesignPanel from '../components/ClassroomDesignPanel.vue'

const confirmMock = vi.hoisted(() => vi.fn())

vi.mock('@shared/composables', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return { ...actual, useConfirmDialog: () => ({ confirm: confirmMock }) }
})

const AGENTS = [
  { id: 'a_design', name: 'Designer' },
  { id: 'a_teacher', name: 'Teacher' },
  { id: 'a_peer', name: 'Peer' },
]
const TYPES = [{ id: 't_crat', name: 'Word puzzle' }]

function run(over: Record<string, unknown> = {}) {
  return {
    id: 'run_1',
    status: 'succeeded',
    design_agent_id: 'a_design',
    activity_type_id: 't_crat',
    activity_name: 'Word puzzle',
    difficulty: 'introductory',
    rounds: 3,
    peer_error_pattern: 'one_in_three',
    tone: 'warm',
    form_notes: '',
    teacher: { agent_id: 'a_teacher', current_prompt: 'T now', current_version: 4, proposed_prompt: 'T draft' },
    peer: { agent_id: 'a_peer', current_prompt: 'P now', current_version: 9, proposed_prompt: 'P draft' },
    notes: 'Made the teacher warmer.',
    failure_reason: null,
    created_at: '2026-11-09T00:00:00Z',
    finished_at: '2026-11-09T00:01:00Z',
    applied_at: null,
    ...over,
  }
}

function problem(status: number, slug: string) {
  return HttpResponse.json(
    { type: `https://smap.dev/problems/${slug}`, title: 'x', status },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  )
}

function seed(latest: ReturnType<typeof run> | null): void {
  server.use(
    http.get('/api/projects/p1/agents', () => HttpResponse.json(AGENTS)),
    http.get('/api/projects/p1/activity-types', () => HttpResponse.json(TYPES)),
    http.get('/api/projects/p1/classroom-design/runs', () => HttpResponse.json(latest ? [latest] : [])),
  )
}

async function mount() {
  const wrapper = await renderView(ClassroomDesignPanel, { props: { projectId: 'p1' } })
  await flushPromises()
  return wrapper
}

describe('ClassroomDesignPanel', () => {
  afterEach(() => confirmMock.mockReset())

  it('submits the form with the chosen agents and activity', async () => {
    seed(null)
    let body: Record<string, unknown> | null = null
    const created = run({ status: 'pending', notes: null })
    server.use(
      http.post('/api/projects/p1/classroom-design/runs', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(created, { status: 202 })
      }),
      // The panel refetches after creating; the server then lists the new run.
      http.get('/api/projects/p1/classroom-design/runs', () => HttpResponse.json(body ? [created] : [])),
    )
    const wrapper = await mount()
    const selects = wrapper.findAll('[data-testid="design-form"] select')
    await selects[0]!.setValue('a_design')
    await selects[1]!.setValue('t_crat')
    await selects[2]!.setValue('a_teacher')
    await selects[3]!.setValue('a_peer')
    await wrapper.find('[data-testid="design-form"]').trigger('submit')
    // Schema validation and the mutation settle over several ticks.
    await vi.waitFor(() => expect(wrapper.find('[data-testid="design-in-flight"]').exists()).toBe(true))

    expect(body).toMatchObject({
      design_agent_id: 'a_design',
      teacher_agent_id: 'a_teacher',
      peer_agent_id: 'a_peer',
      activity_type_id: 't_crat',
      rounds: 3,
    })
  })

  it('refuses the same agent as teacher and peer before calling the server', async () => {
    seed(null)
    const create = vi.fn(() => HttpResponse.json(run()))
    server.use(http.post('/api/projects/p1/classroom-design/runs', create))
    const wrapper = await mount()
    const selects = wrapper.findAll('[data-testid="design-form"] select')
    await selects[0]!.setValue('a_design')
    await selects[1]!.setValue('t_crat')
    await selects[2]!.setValue('a_teacher')
    await selects[3]!.setValue('a_teacher')
    await wrapper.find('[data-testid="design-form"]').trigger('submit')
    await vi.waitFor(() => expect(wrapper.text()).toContain('dashboard.design.sameAgent'))
    await flushPromises()

    expect(create).not.toHaveBeenCalled()
  })

  it('shows current and proposed prompts side by side for a finished run', async () => {
    seed(run())
    const wrapper = await mount()
    expect(wrapper.find('[data-testid="design-current-teacher"]').text()).toBe('T now')
    expect(wrapper.find('[data-testid="design-proposed-teacher"]').text()).toBe('T draft')
    expect(wrapper.find('[data-testid="design-current-peer"]').text()).toBe('P now')
    expect(wrapper.find('[data-testid="design-proposed-peer"]').text()).toBe('P draft')
    expect(wrapper.text()).toContain('Made the teacher warmer.')
    expect(wrapper.find('[data-testid="design-form"]').exists()).toBe(false)
  })

  it('applies after confirming', async () => {
    seed(run())
    const apply = vi.fn(() =>
      HttpResponse.json({ run: run({ status: 'applied', applied_at: '2026-11-09T00:02:00Z' }), teacher_version: 5, peer_version: 10 }),
    )
    server.use(http.post('/api/projects/p1/classroom-design/runs/run_1/apply', apply))
    confirmMock.mockResolvedValue(true)
    const wrapper = await mount()
    await wrapper.find('[data-testid="design-apply"]').trigger('click')
    await flushPromises()

    expect(apply).toHaveBeenCalledOnce()
    expect(wrapper.find('[data-testid="design-applied"]').exists()).toBe(true)
  })

  it('does not apply when the confirmation is declined', async () => {
    seed(run())
    const apply = vi.fn(() => HttpResponse.json({}))
    server.use(http.post('/api/projects/p1/classroom-design/runs/run_1/apply', apply))
    confirmMock.mockResolvedValue(false)
    const wrapper = await mount()
    await wrapper.find('[data-testid="design-apply"]').trigger('click')
    await flushPromises()

    expect(apply).not.toHaveBeenCalled()
  })

  it('shows the conflict and offers a new design when an agent changed', async () => {
    seed(run())
    server.use(
      http.post('/api/projects/p1/classroom-design/runs/run_1/apply', () =>
        problem(409, 'agents/design-apply-conflict'),
      ),
    )
    confirmMock.mockResolvedValue(true)
    const wrapper = await mount()
    await wrapper.find('[data-testid="design-apply"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="design-conflict"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="design-form"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="design-proposal"]').exists()).toBe(false)
  })

  it('shows the reason of a failed run', async () => {
    seed(run({ status: 'failed', failure_reason: 'design_bad_output', teacher: { ...run().teacher, proposed_prompt: null } }))
    const wrapper = await mount()
    expect(wrapper.find('[data-testid="design-failed"]').text()).toContain('dashboard.design.failure.design_bad_output')
    expect(wrapper.find('[data-testid="design-form"]').exists()).toBe(true)
  })

  it('shows a run in flight instead of the form', async () => {
    seed(run({ status: 'running' }))
    const wrapper = await mount()
    expect(wrapper.find('[data-testid="design-in-flight"]').text()).toContain('dashboard.design.status.running')
    expect(wrapper.find('[data-testid="design-form"]').exists()).toBe(false)
  })
})
