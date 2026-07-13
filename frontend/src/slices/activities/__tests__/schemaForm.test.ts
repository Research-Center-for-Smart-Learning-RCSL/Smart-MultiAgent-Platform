// AC-2: schema-driven field derivation, payload assembly, client-side (UX-only)
// validation, and the unsupported-construct -> JSON textarea fallback.

import { describe, expect, it } from 'vitest'
import {
  assemblePayload,
  fieldsFromSchema,
  validatePayload,
} from '../components/schemaFields'
import type { JSONSchema } from '../sdk/types'

const schema: JSONSchema = {
  type: 'object',
  required: ['name', 'rating'],
  properties: {
    name: { type: 'string', title: 'Name' },
    rating: { type: 'number' },
    agree: { type: 'boolean' },
    color: { enum: ['red', 'green'] },
    tags: { type: 'array', items: { enum: ['a', 'b', 'c'] } },
    freeform: { type: 'object' }, // unsupported -> json fallback
  },
}

describe('fieldsFromSchema', () => {
  it('maps each supported property to its kind and degrades unsupported to json', () => {
    const byName = Object.fromEntries(fieldsFromSchema(schema).map((f) => [f.name, f]))
    expect(byName.name!.kind).toBe('string')
    expect(byName.rating!.kind).toBe('number')
    expect(byName.agree!.kind).toBe('boolean')
    expect(byName.color!.kind).toBe('enum')
    expect(byName.color!.options).toEqual(['red', 'green'])
    expect(byName.tags!.kind).toBe('enum-array')
    expect(byName.tags!.options).toEqual(['a', 'b', 'c'])
    // No field dropped — the unsupported object property survives as a json field.
    expect(byName.freeform!.kind).toBe('json')
  })

  it('uses the property title as label, falling back to the key', () => {
    const fields = fieldsFromSchema(schema)
    expect(fields.find((f) => f.name === 'name')!.label).toBe('Name')
    expect(fields.find((f) => f.name === 'rating')!.label).toBe('rating')
  })

  it('returns no fields for a schema without properties', () => {
    expect(fieldsFromSchema({ type: 'object' })).toEqual([])
    expect(fieldsFromSchema(null)).toEqual([])
  })
})

describe('assemblePayload', () => {
  const fields = fieldsFromSchema(schema)

  it('coerces values by kind and parses json fields', () => {
    const { payload, fieldErrors } = assemblePayload(fields, {
      name: 'Ada',
      rating: '5',
      agree: true,
      color: 'red',
      tags: ['a', 'c'],
      freeform: '{"k":1}',
    })
    expect(fieldErrors).toEqual({})
    expect(payload).toEqual({
      name: 'Ada',
      rating: 5,
      agree: true,
      color: 'red',
      tags: ['a', 'c'],
      freeform: { k: 1 },
    })
  })

  it('reports a field error for unparseable json instead of dropping it silently', () => {
    const { fieldErrors } = assemblePayload(fields, { name: 'Ada', rating: '5', freeform: '{not json' })
    expect(fieldErrors.freeform).toBe('invalidJson')
  })

  it('omits empty optional values', () => {
    const { payload } = assemblePayload(fields, { name: 'Ada', rating: '1' })
    expect('color' in payload).toBe(false)
    expect(payload.tags).toEqual([])
  })
})

describe('validatePayload', () => {
  it('flags a missing required field (UX-only mirror of the server rules)', () => {
    const errors = validatePayload(schema, { name: '' })
    expect(errors.name).toBe('fieldInvalid')
    expect(errors.rating).toBe('fieldInvalid')
  })

  it('passes a fully valid payload', () => {
    const errors = validatePayload(schema, { name: 'Ada', rating: 5 })
    expect(errors).toEqual({})
  })
})
