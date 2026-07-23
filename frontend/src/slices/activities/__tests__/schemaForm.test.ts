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
    // An untouched optional enum-array is omitted, not asserted as [] — else a
    // minItems constraint on the optional array 422s a submission the participant
    // never made (V-7).
    expect('tags' in payload).toBe(false)
  })

  it('emits a touched optional enum-array with its selections', () => {
    const { payload } = assemblePayload(fields, { name: 'Ada', rating: '1', tags: ['a'] })
    expect(payload.tags).toEqual(['a'])
  })
})

describe('required enum-array retention', () => {
  const requiredTags: JSONSchema = {
    type: 'object',
    required: ['tags'],
    properties: {
      tags: { type: 'array', items: { enum: ['a', 'b'] } },
    },
  }

  it('keeps a required enum-array so the client min(1) check still flags it', () => {
    const fields = fieldsFromSchema(requiredTags)
    // Present (as []) so zod's .min(1) reports it specifically, rather than
    // disappearing into the generic required-presence branch.
    expect('tags' in assemblePayload(fields, { tags: [] }).payload).toBe(true)
    expect(validatePayload(requiredTags, assemblePayload(fields, { tags: [] }).payload).tags).toBe(
      'fieldInvalid',
    )
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

describe('type-preserving enums', () => {
  const numericEnum: JSONSchema = {
    type: 'object',
    properties: { level: { enum: [1, 2, 3] } },
  }

  it('keeps the declared value type of a numeric enum instead of stringifying', () => {
    const field = fieldsFromSchema(numericEnum)[0]!
    expect(field.options).toEqual([1, 2, 3])
    const { payload } = assemblePayload([field], { level: 2 })
    expect(payload.level).toBe(2) // number, not '2'
  })

  it('validates numeric-enum membership', () => {
    expect(validatePayload(numericEnum, { level: 2 })).toEqual({})
    expect(validatePayload(numericEnum, { level: 5 }).level).toBe('fieldInvalid')
  })
})

describe('optional string omission', () => {
  const optionalString: JSONSchema = {
    type: 'object',
    properties: { note: { type: 'string' } }, // not required
  }

  it('omits a blank optional string so a string constraint is not tripped', () => {
    const fields = fieldsFromSchema(optionalString)
    expect('note' in assemblePayload(fields, { note: '' }).payload).toBe(false)
    expect(assemblePayload(fields, { note: 'hi' }).payload.note).toBe('hi')
  })
})

describe('required JSON-fallback enforcement', () => {
  const requiredJson: JSONSchema = {
    type: 'object',
    required: ['meta'],
    properties: { meta: { type: 'object' } }, // unsupported -> json fallback
  }

  it('flags an empty required JSON field and accepts a present one', () => {
    const fields = fieldsFromSchema(requiredJson)
    expect('meta' in assemblePayload(fields, { meta: '' }).payload).toBe(false)
    expect(validatePayload(requiredJson, {}).meta).toBe('fieldInvalid')
    expect(validatePayload(requiredJson, { meta: { a: 1 } })).toEqual({})
  })
})
