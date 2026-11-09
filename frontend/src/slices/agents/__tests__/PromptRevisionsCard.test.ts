// Prompt history on the agent page ([R34.07], AC-17). The test i18n harness echoes keys.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import PromptRevisionsCard from '../components/PromptRevisionsCard.vue'

const confirmMock = vi.hoisted(() => vi.fn())
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock('@shared/composables', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return { ...actual, useConfirmDialog: () => ({ confirm: confirmMock }), useToast: () => toastMock }
})

function revision(over: Record<string, unknown> = {}) {
  return {
    id: 'rev_1',
    agent_version: 4,
    system_prompt: 'old prompt',
    source: 'manual',
    actor_user_id: 'u1',
    design_run_id: null,
    restored_from_id: null,
    created_at: '2026-11-09T00:00:00Z',
    ...over,
  }
}

const PROPS = { agentId: 'agent_1', version: 5, currentPrompt: 'new prompt', dirty: false }

describe('PromptRevisionsCard', () => {
  afterEach(() => {
    confirmMock.mockReset()
    toastMock.error.mockReset()
    toastMock.success.mockReset()
  })

  it('shows the empty state before the prompt ever changed', async () => {
    server.use(http.get('/api/agents/agent_1/prompt-revisions', () => HttpResponse.json([])))
    const wrapper = await renderView(PromptRevisionsCard, { props: PROPS })
    await flushPromises()
    expect(wrapper.text()).toContain('agents.revisions.empty')
    expect(wrapper.findAll('[data-testid="prompt-revision"]')).toHaveLength(0)
  })

  it('marks the current prompt and offers restore only for the others', async () => {
    server.use(
      http.get('/api/agents/agent_1/prompt-revisions', () =>
        HttpResponse.json([
          revision({ id: 'rev_2', agent_version: 5, system_prompt: 'new prompt', source: 'design' }),
          revision(),
        ]),
      ),
    )
    const wrapper = await renderView(PromptRevisionsCard, { props: PROPS })
    await flushPromises()
    const rows = wrapper.findAll('[data-testid="prompt-revision"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('agents.revisions.source.design')
    expect(rows[0]!.find('[data-testid="prompt-revision-current"]').exists()).toBe(true)
    expect(rows[0]!.find('[data-testid="prompt-revision-restore"]').exists()).toBe(false)
    expect(rows[1]!.find('[data-testid="prompt-revision-restore"]').exists()).toBe(true)
  })

  it('restores with the version it was shown, after confirming', async () => {
    let restored: { url: string; ifMatch: string | null } | null = null
    server.use(
      http.get('/api/agents/agent_1/prompt-revisions', () => HttpResponse.json([revision()])),
      http.post('/api/agents/agent_1/prompt-revisions/rev_1/restore', ({ request }) => {
        restored = { url: request.url, ifMatch: request.headers.get('If-Match') }
        return HttpResponse.json({})
      }),
    )
    confirmMock.mockResolvedValue(true)
    const wrapper = await renderView(PromptRevisionsCard, { props: { ...PROPS, dirty: true } })
    await flushPromises()
    await wrapper.find('[data-testid="prompt-revision-restore"]').trigger('click')
    await flushPromises()
    expect(confirmMock.mock.calls[0]![0].message).toBe('agents.revisions.confirmDirty')
    expect(restored).not.toBeNull()
    expect(restored!.ifMatch).toBe('5')
  })

  it('reports a concurrent edit as a conflict and reloads the agent', async () => {
    server.use(
      http.get('/api/agents/agent_1/prompt-revisions', () => HttpResponse.json([revision()])),
      http.post('/api/agents/agent_1/prompt-revisions/rev_1/restore', () =>
        HttpResponse.json(
          { type: 'https://smap.dev/problems/agents/version-mismatch', title: 'x', status: 412 },
          { status: 412, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    )
    confirmMock.mockResolvedValue(true)
    const wrapper = await renderView(PromptRevisionsCard, { props: PROPS })
    await flushPromises()
    await wrapper.find('[data-testid="prompt-revision-restore"]').trigger('click')
    await vi.waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('agents.revisions.conflict'))
  })

  it('does nothing when the restore is not confirmed', async () => {
    const restore = vi.fn(() => HttpResponse.json({}))
    server.use(
      http.get('/api/agents/agent_1/prompt-revisions', () => HttpResponse.json([revision()])),
      http.post('/api/agents/agent_1/prompt-revisions/rev_1/restore', restore),
    )
    confirmMock.mockResolvedValue(false)
    const wrapper = await renderView(PromptRevisionsCard, { props: PROPS })
    await flushPromises()
    await wrapper.find('[data-testid="prompt-revision-restore"]').trigger('click')
    await flushPromises()
    expect(restore).not.toHaveBeenCalled()
  })
})
