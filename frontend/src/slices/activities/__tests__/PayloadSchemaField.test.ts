// Covers the raw-JSON escape hatch (FU-5): mode toggle, Builder->Raw seeding,
// parse feedback, and the one-way lock (Q-1a). SCodeEditor is stubbed to a bare
// textarea so these tests never mount CodeMirror.

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { i18n } from '@shared/i18n'

import PayloadSchemaField from '../components/PayloadSchemaField.vue'
import type { JSONSchema } from '../sdk/types'

const SCodeEditorStub = {
  name: 'SCodeEditor',
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template:
    '<textarea data-testid="raw-editor" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)"></textarea>',
}

function mountField(initial: JSONSchema | null = null) {
  return mount(PayloadSchemaField, {
    props: { initial },
    global: { plugins: [i18n], stubs: { SCodeEditor: SCodeEditorStub } },
  })
}

const NESTED: JSONSchema = {
  type: 'object',
  properties: { profile: { type: 'object', properties: { age: { type: 'integer' } } } },
}

describe('PayloadSchemaField', () => {
  it('defaults to Builder mode', () => {
    const w = mountField()
    expect(w.findComponent({ name: 'SchemaBuilder' }).exists()).toBe(true)
    expect(w.findComponent({ name: 'SCodeEditor' }).exists()).toBe(false)
    expect(w.find('[data-testid="schema-mode-builder"]').attributes('aria-pressed')).toBe('true')
  })

  it('seeds the raw editor with the builder schema on first switch (AC-3)', async () => {
    const w = mountField()
    await w.find('[data-testid="schema-field-name"]').setValue('answer')
    await w.find('[data-testid="schema-mode-raw"]').trigger('click')

    const editor = w.findComponent({ name: 'SCodeEditor' })
    expect(editor.exists()).toBe(true)
    expect(w.findComponent({ name: 'SchemaBuilder' }).exists()).toBe(false)
    expect(String(editor.props('modelValue'))).toContain('"answer"')
  })

  it('does not lock the builder merely by switching to raw (Q-1a)', async () => {
    const w = mountField()
    await w.find('[data-testid="schema-mode-raw"]').trigger('click')
    expect(w.find('[data-testid="schema-mode-builder"]').attributes('disabled')).toBeUndefined()
  })

  it('locks the builder once the raw value is edited (Q-1a, AC-3)', async () => {
    const w = mountField()
    await w.find('[data-testid="schema-mode-raw"]').trigger('click')
    await w.findComponent({ name: 'SCodeEditor' }).vm.$emit('update:modelValue', '{"type":"object"}')
    expect(w.find('[data-testid="schema-mode-builder"]').attributes('disabled')).toBeDefined()
  })

  it('reports a parse error and blocks submit for invalid JSON (AC-2)', async () => {
    const w = mountField()
    await w.find('[data-testid="schema-mode-raw"]').trigger('click')
    await w.findComponent({ name: 'SCodeEditor' }).vm.$emit('update:modelValue', '{ not json')

    expect(w.emitted('update:parseError')?.at(-1)).toEqual(['schemaInvalidJson'])
    // An empty-properties object fails the form's ">=1 property" gate.
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([{ type: 'object', properties: {} }])
  })

  it('emits the parsed schema for a valid nested value (AC-1)', async () => {
    const w = mountField()
    await w.find('[data-testid="schema-mode-raw"]').trigger('click')
    await w.findComponent({ name: 'SCodeEditor' }).vm.$emit('update:modelValue', JSON.stringify(NESTED))

    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([NESTED])
    expect(w.emitted('update:parseError')?.at(-1)).toEqual([null])
  })

  it('opens a non-flat stored schema in Raw mode with the builder locked (AC-3)', () => {
    const w = mountField(NESTED)
    expect(w.findComponent({ name: 'SCodeEditor' }).exists()).toBe(true)
    expect(w.findComponent({ name: 'SchemaBuilder' }).exists()).toBe(false)
    expect(w.find('[data-testid="schema-mode-builder"]').attributes('disabled')).toBeDefined()
    expect(String(w.findComponent({ name: 'SCodeEditor' }).props('modelValue'))).toContain('"profile"')
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([NESTED])
  })

  it('opens a flat stored schema in Builder mode (AC-3)', () => {
    const flat: JSONSchema = { type: 'object', properties: { answer: { type: 'string' } }, required: ['answer'] }
    const w = mountField(flat)
    expect(w.findComponent({ name: 'SchemaBuilder' }).exists()).toBe(true)
    expect(w.findComponent({ name: 'SCodeEditor' }).exists()).toBe(false)
  })
})
