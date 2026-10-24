import { describe, it, expect } from 'vitest'
import { z } from 'zod'
import { toTypedSchema } from '../zodTypedSchema'

const schema = z.object({
  name: z.string().min(1, 'nameRequired'),
  note: z.string().default('none'),
  items: z.array(z.object({ label: z.string().min(1, 'labelRequired') })),
  nested: z.object({ count: z.number().default(3), flag: z.boolean() }),
  maybe: z.string().optional(),
})

describe('toTypedSchema', () => {
  it('returns the parsed value with defaults applied on success', async () => {
    const result = await toTypedSchema(schema).parse({
      name: 'a',
      items: [],
      nested: { flag: true },
    })
    expect(result.errors).toEqual([])
    expect(result.value).toEqual({ name: 'a', note: 'none', items: [], nested: { count: 3, flag: true } })
  })

  it('maps issue paths to vee-validate paths, with brackets for array indexes', async () => {
    const result = await toTypedSchema(schema).parse({
      name: '',
      items: [{ label: 'ok' }, { label: '' }],
      nested: { flag: true },
    })
    expect(result.value).toBeUndefined()
    expect(result.errors).toEqual([
      { path: 'name', errors: ['nameRequired'] },
      { path: 'items[1].label', errors: ['labelRequired'] },
    ])
  })

  it('surfaces the branch issues of a failed union', async () => {
    const union = z.object({ v: z.union([z.string().min(2, 'tooShort'), z.number()]) })
    const result = await toTypedSchema(union).parse({ v: 'x' })
    expect(result.errors.map((e) => e.path)).toContain('v')
    expect(result.errors.flatMap((e) => e.errors)).toContain('tooShort')
  })

  it('casts an invalid value by merging declared defaults under it', () => {
    const cast = toTypedSchema(schema).cast!({ name: '', nested: { flag: false } })
    expect(cast).toEqual({
      name: '',
      note: 'none',
      items: undefined,
      maybe: undefined,
      nested: { count: 3, flag: false },
    })
  })

  it('describes required, optional, defaulted and missing paths', () => {
    const typed = toTypedSchema(schema)
    expect(typed.describe!('name')).toEqual({ required: true, exists: true })
    expect(typed.describe!('note')).toEqual({ required: false, exists: true })
    expect(typed.describe!('maybe')).toEqual({ required: false, exists: true })
    expect(typed.describe!('nested.flag')).toEqual({ required: true, exists: true })
    expect(typed.describe!('items[0].label')).toEqual({ required: true, exists: true })
    expect(typed.describe!('nope' as 'name')).toEqual({ required: false, exists: false })
  })
})
