// AC-3 (webhook create), AC-4 (mcp sub-form), AC-5 (409 surfaced on the form),
// AC-8 (in_process not offered). The api + agents slice are mocked.

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'

import type * as AgentsSlice from '@slices/agents'
import { renderView } from '../../../../tests/utils'
import ActivityTypeForm from '../components/ActivityTypeForm.vue'

const registerMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({ registerActivityType: registerMock }))

vi.mock('@slices/agents', async (importOriginal) => ({
  ...(await importOriginal<typeof AgentsSlice>()),
  agentsApi: {
    list: vi.fn().mockResolvedValue([{ id: 'a1', name: 'Agent 1' }]),
    listTools: vi.fn().mockResolvedValue([
      { id: 'b1', tool_type: 'hosted_mcp', display_name: 'Binding 1' },
      { id: 'b2', tool_type: 'hosted_web_search', display_name: 'Not a binding' },
    ]),
  },
  agentKeys: {
    agents: (projectId: string) => ['agents', 'list', projectId],
    tools: (agentId: string) => ['agents', 'tools', agentId],
  },
}))

async function mountForm() {
  return renderView(ActivityTypeForm, { props: { projectId: 'p1', open: true } })
}

async function fillValidWebhook(wrapper: Awaited<ReturnType<typeof mountForm>>) {
  await wrapper.find('[data-testid="type-key"]').setValue('quiz')
  await wrapper.find('[data-testid="type-name"]').setValue('Quiz')
  await wrapper.find('[data-testid="schema-field-name"]').setValue('answer')
  await wrapper.find('[data-testid="type-webhook-url"]').setValue('https://x.test/score')
}

beforeEach(() => registerMock.mockReset())

describe('ActivityTypeForm', () => {
  it('offers only webhook and mcp validator kinds (AC-8)', async () => {
    const wrapper = await mountForm()
    const values = wrapper
      .find('[data-testid="type-validator"]')
      .findAll('option')
      .map((o) => o.attributes('value'))
    expect(values).toContain('webhook')
    expect(values).toContain('mcp')
    expect(values).not.toContain('in_process')
  })

  it('swaps the sub-form between webhook and mcp (AC-3, AC-4)', async () => {
    const wrapper = await mountForm()
    expect(wrapper.find('[data-testid="type-webhook-url"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="type-mcp-agent"]').exists()).toBe(false)

    await wrapper.find('[data-testid="type-validator"]').setValue('mcp')
    expect(wrapper.find('[data-testid="type-webhook-url"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="type-mcp-agent"]').exists()).toBe(true)
  })

  it('registers a webhook type with the assembled body (AC-3)', async () => {
    registerMock.mockResolvedValue({ id: 't1' })
    const wrapper = await mountForm()
    await fillValidWebhook(wrapper)

    await wrapper.find('form').trigger('submit')

    // vee-validate's handleSubmit resolves across several ticks; poll rather than
    // assert on the next tick (mirrors AgentToolsView.test).
    await vi.waitFor(() => expect(registerMock).toHaveBeenCalled())
    expect(registerMock).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({
        key: 'quiz',
        name: 'Quiz',
        validator_kind: 'webhook',
        validator_config: { url: 'https://x.test/score' },
        payload_schema: expect.objectContaining({
          type: 'object',
          properties: { answer: { type: 'string' } },
        }),
      }),
    )
    await flushPromises()
    expect(wrapper.emitted('created')).toBeTruthy()
  })
})
