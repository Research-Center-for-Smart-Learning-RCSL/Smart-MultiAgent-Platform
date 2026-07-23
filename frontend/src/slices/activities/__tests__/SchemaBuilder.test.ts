// AC-5: the guided builder emits a valid JSON Schema `object` from field rows.

import { describe, expect, it } from 'vitest'

import { renderView } from '../../../../tests/utils'
import SchemaBuilder from '../components/SchemaBuilder.vue'

describe('SchemaBuilder', () => {
  it('emits an object schema whose required list tracks the field toggle', async () => {
    const wrapper = await renderView(SchemaBuilder)

    // The default row is required; naming it produces a required property.
    await wrapper.find('[data-testid="schema-field-name"]').setValue('answer')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    expect(emitted!.at(-1)![0]).toEqual({
      type: 'object',
      properties: { answer: { type: 'string' } },
      required: ['answer'],
    })
  })

  it('renders a JSON preview of the assembled schema', async () => {
    const wrapper = await renderView(SchemaBuilder)
    await wrapper.find('[data-testid="schema-field-name"]').setValue('score')

    const preview = wrapper.find('[data-testid="schema-preview"]')
    expect(preview.text()).toContain('"score"')
    expect(preview.text()).toContain('"type": "object"')
  })

  it('omits an unnamed row from the emitted schema', async () => {
    const wrapper = await renderView(SchemaBuilder)
    // No name typed → the row contributes nothing; properties stays empty.
    const emitted = wrapper.emitted('update:modelValue')!
    expect(emitted.at(-1)![0]).toEqual({ type: 'object', properties: {} })
  })
})
