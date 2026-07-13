// AC-2 at the component level: each supported field kind renders its control,
// an unsupported construct renders a labelled textarea (no field dropped), and a
// valid submit emits the assembled object.

import { describe, expect, it } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import SchemaForm from '../components/SchemaForm.vue'
import type { JSONSchema } from '../sdk/types'

const schema: JSONSchema = {
  type: 'object',
  required: ['name'],
  properties: {
    name: { type: 'string', title: 'Name' },
    rating: { type: 'number' },
    agree: { type: 'boolean' },
    color: { enum: ['red', 'green'] },
    tags: { type: 'array', items: { enum: ['a', 'b'] } },
    freeform: { type: 'object' },
  },
}

describe('SchemaForm rendering', () => {
  it('renders a control for every property including a textarea for the unsupported one', async () => {
    const wrapper = await renderView(SchemaForm, { props: { schema } })
    // string + number both render as text inputs (number as text avoids SInput's
    // empty -> 0 coercion).
    expect(wrapper.findAll('input[type="text"]').length).toBeGreaterThanOrEqual(2)
    expect(wrapper.find('select').exists()).toBe(true)
    expect(wrapper.findAll('input[type="checkbox"]').length).toBeGreaterThanOrEqual(3) // agree + 2 tags
    // The unsupported object property survives as a JSON textarea.
    expect(wrapper.find('textarea').exists()).toBe(true)
  })

  it('blocks submit while a required field is empty, then emits once valid', async () => {
    const wrapper = await renderView(SchemaForm, { props: { schema } })

    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.emitted('submit')).toBeUndefined()

    await wrapper.find('input[type="text"]').setValue('Ada')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const emitted = wrapper.emitted('submit')
    expect(emitted).toBeTruthy()
    expect(emitted![0]![0]).toMatchObject({ name: 'Ada' })
  })
})
