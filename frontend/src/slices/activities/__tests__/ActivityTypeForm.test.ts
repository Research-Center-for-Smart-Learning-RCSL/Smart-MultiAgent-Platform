// AC-3 (webhook create), AC-4 (mcp sub-form), AC-5 (409 surfaced on the form),
// plus the in-process-validators feature: the in_process kind is gated on ≥1
// registered validator and the exact_match sub-form assembles its config. The api
// + agents slice are mocked.

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'

import type * as AgentsSlice from '@slices/agents'
import { renderView } from '../../../../tests/utils'
import ActivityTypeForm from '../components/ActivityTypeForm.vue'

const registerMock = vi.hoisted(() => vi.fn())
const updateMock = vi.hoisted(() => vi.fn())
const listValidatorsMock = vi.hoisted(() => vi.fn())
const policyMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({
  registerActivityType: registerMock,
  updateActivityType: updateMock,
  listActivityValidators: listValidatorsMock,
  getActivityPolicy: policyMock,
}))

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

const EDIT_TYPE = {
  id: 't1',
  project_id: 'p1',
  key: 'quiz',
  name: 'Quiz',
  payload_schema: { type: 'object', properties: { answer: { type: 'string' } }, required: ['answer'] },
  validator_kind: 'webhook',
  validator_config: { url: 'https://x.test/score' },
  retention_days: 7,
  expose_payload_to_agent: true,
  echo_includes_content: false,
  created_at: null,
}

beforeEach(() => {
  registerMock.mockReset()
  updateMock.mockReset()
  listValidatorsMock.mockReset()
  listValidatorsMock.mockResolvedValue([{ id: 'exact_match', title: 'Exact match' }])
  policyMock.mockReset()
  // Default: no platform policy saved, so nothing is locked or pre-filled — the
  // behavior every pre-existing test here was written against.
  policyMock.mockResolvedValue({
    expose_payload_to_agent_default: true,
    expose_payload_to_agent_locked: false,
    echo_includes_content_default: false,
    echo_includes_content_locked: false,
    retention_days_default: null,
    retention_days_max: null,
  })
})

describe('ActivityTypeForm', () => {
  it('hides in_process when no validator is registered (AC-4)', async () => {
    listValidatorsMock.mockResolvedValue([])
    const wrapper = await mountForm()
    await flushPromises()
    const values = wrapper
      .find('[data-testid="type-validator"]')
      .findAll('option')
      .map((o) => o.attributes('value'))
    expect(values).toContain('webhook')
    expect(values).toContain('mcp')
    expect(values).not.toContain('in_process')
  })

  it('offers in_process once a validator is registered (AC-4)', async () => {
    const wrapper = await mountForm()
    await flushPromises()
    const values = wrapper
      .find('[data-testid="type-validator"]')
      .findAll('option')
      .map((o) => o.attributes('value'))
    expect(values).toContain('in_process')
  })

  it('assembles the exact_match validator_config from the sub-form (AC-2, AC-4)', async () => {
    registerMock.mockResolvedValue({ id: 't1' })
    const wrapper = await mountForm()
    await flushPromises()

    await wrapper.find('[data-testid="type-key"]').setValue('quiz')
    await wrapper.find('[data-testid="type-name"]').setValue('Quiz')
    await wrapper.find('[data-testid="schema-field-name"]').setValue('answer')
    await wrapper.find('[data-testid="type-validator"]').setValue('in_process')
    await wrapper.find('[data-testid="type-in-process-validator"]').setValue('exact_match')
    // The field picker sources its options from the schema built above.
    await wrapper.find('[data-testid="type-exact-match-field"]').setValue('answer')
    await wrapper.find('[data-testid="type-exact-match-expected"]').setValue('42')

    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => expect(registerMock).toHaveBeenCalled())
    expect(registerMock).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({
        validator_kind: 'in_process',
        validator_config: {
          validator_id: 'exact_match',
          field: 'answer',
          expected: '42',
          case_sensitive: false,
        },
      }),
    )
  })

  it('assembles the filled_count validator_config from the sub-form (AC-6)', async () => {
    listValidatorsMock.mockResolvedValue([
      { id: 'exact_match', title: 'Exact match' },
      { id: 'filled_count', title: 'Filled count' },
    ])
    registerMock.mockResolvedValue({ id: 't1' })
    const wrapper = await mountForm()
    await flushPromises()

    await wrapper.find('[data-testid="type-key"]').setValue('mandala-9grid')
    await wrapper.find('[data-testid="type-name"]').setValue('Mandala')
    await wrapper.find('[data-testid="schema-field-name"]').setValue('center')
    await wrapper.find('[data-testid="type-validator"]').setValue('in_process')
    await wrapper.find('[data-testid="type-in-process-validator"]').setValue('filled_count')
    // One declared field here, so 1 is the highest threshold that can ever be met
    // (a higher one is rejected by its own test below).
    await wrapper.find('[data-testid="type-filled-count-min"]').setValue('1')

    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => expect(registerMock).toHaveBeenCalled())
    expect(registerMock).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({
        validator_kind: 'in_process',
        validator_config: { validator_id: 'filled_count', min_filled: 1 },
      }),
    )
  })

  it('keeps min_filled 0 rather than folding it to a blank (AC-4, AC-6)', async () => {
    listValidatorsMock.mockResolvedValue([{ id: 'filled_count', title: 'Filled count' }])
    registerMock.mockResolvedValue({ id: 't1' })
    const wrapper = await mountForm()
    await flushPromises()

    await wrapper.find('[data-testid="type-key"]').setValue('open-ended')
    await wrapper.find('[data-testid="type-name"]').setValue('Open ended')
    await wrapper.find('[data-testid="schema-field-name"]').setValue('idea')
    await wrapper.find('[data-testid="type-validator"]').setValue('in_process')
    await wrapper.find('[data-testid="type-in-process-validator"]').setValue('filled_count')
    await wrapper.find('[data-testid="type-filled-count-min"]').setValue('0')

    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => expect(registerMock).toHaveBeenCalled())
    expect(registerMock).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({
        validator_config: { validator_id: 'filled_count', min_filled: 0 },
      }),
    )
  })

  it('treats a cleared min_filled box as missing, not as 0', async () => {
    // SInput coerces a type="number" value with Number(), and Number('') === 0,
    // so a numeric input would silently submit min_filled: 0 — turning the
    // activity collect-only when the author only meant to retype the value.
    listValidatorsMock.mockResolvedValue([{ id: 'filled_count', title: 'Filled count' }])
    registerMock.mockResolvedValue({ id: 't1' })
    const wrapper = await mountForm()
    await flushPromises()

    await wrapper.find('[data-testid="type-key"]').setValue('open-ended')
    await wrapper.find('[data-testid="type-name"]').setValue('Open ended')
    await wrapper.find('[data-testid="schema-field-name"]').setValue('idea')
    await wrapper.find('[data-testid="type-validator"]').setValue('in_process')
    await wrapper.find('[data-testid="type-in-process-validator"]').setValue('filled_count')
    await wrapper.find('[data-testid="type-filled-count-min"]').setValue('4')
    await wrapper.find('[data-testid="type-filled-count-min"]').setValue('')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(registerMock).not.toHaveBeenCalled()
  })

  it('rejects a min_filled higher than the number of submission fields', async () => {
    listValidatorsMock.mockResolvedValue([{ id: 'filled_count', title: 'Filled count' }])
    registerMock.mockResolvedValue({ id: 't1' })
    const wrapper = await mountForm()
    await flushPromises()

    await wrapper.find('[data-testid="type-key"]').setValue('impossible')
    await wrapper.find('[data-testid="type-name"]').setValue('Impossible')
    await wrapper.find('[data-testid="schema-field-name"]').setValue('only_field')
    await wrapper.find('[data-testid="type-validator"]').setValue('in_process')
    await wrapper.find('[data-testid="type-in-process-validator"]').setValue('filled_count')
    // One declared field, threshold of 5: no submission could ever score valid.
    await wrapper.find('[data-testid="type-filled-count-min"]').setValue('5')

    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() =>
      expect(wrapper.text()).toContain('activities.typeForm.filledCountExceeds'),
    )
    expect(registerMock).not.toHaveBeenCalled()
  })

  it('disables a governance field the platform policy locks (AC-11)', async () => {
    policyMock.mockResolvedValue({
      expose_payload_to_agent_default: false,
      expose_payload_to_agent_locked: true,
      echo_includes_content_default: false,
      echo_includes_content_locked: false,
      retention_days_default: null,
      retention_days_max: null,
    })
    const wrapper = await mountForm()
    await flushPromises()

    // SCheckbox puts the testid on its <label> root; the control is inside.
    const expose = wrapper.find('[data-testid="type-expose-payload-to-agent"] input')
      .element as HTMLInputElement
    const echo = wrapper.find('[data-testid="type-echo-includes-content"] input')
      .element as HTMLInputElement
    expect(expose.disabled).toBe(true)
    // Only the locked one is disabled.
    expect(echo.disabled).toBe(false)
    expect(wrapper.text()).toContain('activities.typeForm.lockedByPolicy')
  })

  it('pre-fills a new type from the policy defaults, unlocked (AC-11)', async () => {
    policyMock.mockResolvedValue({
      expose_payload_to_agent_default: false,
      expose_payload_to_agent_locked: false,
      echo_includes_content_default: true,
      echo_includes_content_locked: false,
      retention_days_default: 30,
      retention_days_max: null,
    })
    const wrapper = await mountForm()
    await flushPromises()

    // SCheckbox puts the testid on its <label> root; the control is inside.
    const expose = wrapper.find('[data-testid="type-expose-payload-to-agent"] input')
      .element as HTMLInputElement
    // Pre-filled from the default, but still editable: an unlocked field is a
    // suggestion, not a constraint.
    expect(expose.checked).toBe(false)
    expect(expose.disabled).toBe(false)
  })

  it('leaves a permissive policy alone (AC-7)', async () => {
    const wrapper = await mountForm()
    await flushPromises()

    // SCheckbox puts the testid on its <label> root; the control is inside.
    const expose = wrapper.find('[data-testid="type-expose-payload-to-agent"] input')
      .element as HTMLInputElement
    expect(expose.disabled).toBe(false)
    expect(wrapper.text()).not.toContain('activities.typeForm.lockedByPolicy')
  })

  it('shows the min_filled input only for filled_count (AC-6)', async () => {
    listValidatorsMock.mockResolvedValue([
      { id: 'exact_match', title: 'Exact match' },
      { id: 'filled_count', title: 'Filled count' },
    ])
    const wrapper = await mountForm()
    await flushPromises()

    await wrapper.find('[data-testid="type-validator"]').setValue('in_process')
    await wrapper.find('[data-testid="type-in-process-validator"]').setValue('exact_match')
    expect(wrapper.find('[data-testid="type-filled-count-min"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="type-exact-match-field"]').exists()).toBe(true)

    await wrapper.find('[data-testid="type-in-process-validator"]').setValue('filled_count')
    expect(wrapper.find('[data-testid="type-filled-count-min"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="type-exact-match-field"]').exists()).toBe(false)
  })

  it('swaps the sub-form between webhook and mcp (AC-3, AC-4)', async () => {
    const wrapper = await mountForm()
    expect(wrapper.find('[data-testid="type-webhook-url"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="type-mcp-agent"]').exists()).toBe(false)

    await wrapper.find('[data-testid="type-validator"]').setValue('mcp')
    expect(wrapper.find('[data-testid="type-webhook-url"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="type-mcp-agent"]').exists()).toBe(true)
  })

  it('defaults agent visibility on and participant visibility off (agent-visibility follow-up)', async () => {
    registerMock.mockResolvedValue({ id: 't1' })
    const wrapper = await mountForm()
    await fillValidWebhook(wrapper)

    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => expect(registerMock).toHaveBeenCalled())
    expect(registerMock).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({ expose_payload_to_agent: true, echo_includes_content: false }),
    )
  })

  it('submits toggled agent/participant visibility (agent-visibility follow-up)', async () => {
    registerMock.mockResolvedValue({ id: 't1' })
    const wrapper = await mountForm()
    await fillValidWebhook(wrapper)

    await wrapper
      .find('[data-testid="type-expose-payload-to-agent"] input[type="checkbox"]')
      .trigger('change')
    await wrapper
      .find('[data-testid="type-echo-includes-content"] input[type="checkbox"]')
      .trigger('change')
    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => expect(registerMock).toHaveBeenCalled())
    expect(registerMock).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({ expose_payload_to_agent: false, echo_includes_content: true }),
    )
  })

  it('pre-fills agent/participant visibility from the row in edit mode (agent-visibility follow-up)', async () => {
    const wrapper = await renderView(ActivityTypeForm, {
      props: {
        projectId: 'p1',
        open: true,
        editType: { ...EDIT_TYPE, expose_payload_to_agent: false, echo_includes_content: true },
      },
    })
    await flushPromises()

    const exposeInput = wrapper.find(
      '[data-testid="type-expose-payload-to-agent"] input[type="checkbox"]',
    ).element as HTMLInputElement
    const echoInput = wrapper.find(
      '[data-testid="type-echo-includes-content"] input[type="checkbox"]',
    ).element as HTMLInputElement
    expect(exposeInput.checked).toBe(false)
    expect(echoInput.checked).toBe(true)
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

  it('registers a nested payload_schema authored in raw mode (AC-1)', async () => {
    registerMock.mockResolvedValue({ id: 't1' })
    const wrapper = await mountForm()
    await flushPromises()

    await wrapper.find('[data-testid="type-key"]').setValue('survey')
    await wrapper.find('[data-testid="type-name"]').setValue('Survey')
    await wrapper.find('[data-testid="type-webhook-url"]').setValue('https://x.test/score')

    // The raw editor produces a nested schema the flat builder can't express;
    // it reaches the form through the same update:model-value the builder uses.
    const nested = {
      type: 'object',
      properties: { profile: { type: 'object', properties: { age: { type: 'integer' } } } },
    }
    wrapper.findComponent({ name: 'PayloadSchemaField' }).vm.$emit('update:modelValue', nested)
    await flushPromises()

    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => expect(registerMock).toHaveBeenCalled())
    expect(registerMock).toHaveBeenCalledWith(
      'p1',
      expect.objectContaining({ key: 'survey', payload_schema: nested }),
    )
  })

  it('pre-fills from the row and submits a keyless PATCH in edit mode (AC-1)', async () => {
    updateMock.mockResolvedValue({ id: 't1' })
    const wrapper = await renderView(ActivityTypeForm, {
      props: { projectId: 'p1', open: true, editType: EDIT_TYPE },
    })
    await flushPromises()

    const keyField = wrapper.find('[data-testid="type-key"]')
    expect((keyField.element as HTMLInputElement).value).toBe('quiz')
    expect(keyField.attributes('disabled')).toBeDefined() // key is immutable on edit
    expect((wrapper.find('[data-testid="type-name"]').element as HTMLInputElement).value).toBe('Quiz')
    // SchemaBuilder rehydrated the stored field from validator/payload schema.
    expect((wrapper.find('[data-testid="schema-field-name"]').element as HTMLInputElement).value).toBe(
      'answer',
    )

    await wrapper.find('[data-testid="type-name"]').setValue('Quiz v2')
    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => expect(updateMock).toHaveBeenCalled())
    expect(updateMock).toHaveBeenCalledWith(
      'p1',
      't1',
      expect.objectContaining({
        name: 'Quiz v2',
        validator_kind: 'webhook',
        validator_config: { url: 'https://x.test/score' },
        payload_schema: expect.objectContaining({ properties: { answer: { type: 'string' } } }),
      }),
    )
    expect(updateMock.mock.calls[0][2]).not.toHaveProperty('key')
    expect(registerMock).not.toHaveBeenCalled()
    await flushPromises()
    expect(wrapper.emitted('updated')).toBeTruthy()
  })
})
