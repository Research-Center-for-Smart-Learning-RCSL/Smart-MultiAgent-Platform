import { describe, expect, it } from 'vitest'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import { agentCreateSchema } from '../schemas'

// Sampling controls (R9.18) validation at the form layer (AC-6). The backend
// Pydantic model enforces the same bounds (422); this pins the client-side Zod
// mirror so out-of-range values are flagged before submit.

const base = { name: 'AA', model_hint: 'openai', key_group_id: '11111111-1111-1111-1111-111111111111' }

describe('agentCreateSchema sampling controls', () => {
  it('accepts the reproducible-scoring config (temperature=0, top_p=1)', () => {
    const parsed = agentCreateSchema.parse({ ...base, temperature: 0, top_p: 1, seed: 42 })
    // temperature=0 survives as a real value, not collapsed to null (provider default).
    expect(parsed.temperature).toBe(0)
    expect(parsed.top_p).toBe(1)
    expect(parsed.seed).toBe(42)
  })

  it('defaults unset sampling controls to null', () => {
    const parsed = agentCreateSchema.parse({ ...base })
    expect(parsed.temperature).toBeNull()
    expect(parsed.top_p).toBeNull()
    expect(parsed.seed).toBeNull()
  })

  it('coerces an empty string to null (cleared field = provider default)', () => {
    const parsed = agentCreateSchema.parse({ ...base, temperature: '', top_p: '' })
    expect(parsed.temperature).toBeNull()
    expect(parsed.top_p).toBeNull()
  })

  it.each([
    { field: 'temperature', value: 2.5 },
    { field: 'temperature', value: -0.1 },
    { field: 'top_p', value: 1.5 },
    { field: 'top_p', value: -0.1 },
  ])('rejects out-of-range $field=$value', ({ field, value }) => {
    expect(() => agentCreateSchema.parse({ ...base, [field]: value })).toThrow()
  })
})

// context_token_cap upper bound (task dossier:
// docs/tasks/2026-07-16-context-token-cap-upper-bound/, AC-7). Mirrors the backend
// `le=MAX_CONTEXT_TOKEN_CAP` bound via INPUT_LIMITS.CONTEXT_TOKEN_CAP.
describe('agentCreateSchema context_token_cap bound', () => {
  it('accepts the bound value', () => {
    const parsed = agentCreateSchema.parse({
      ...base,
      context_token_cap: INPUT_LIMITS.CONTEXT_TOKEN_CAP,
    })
    expect(parsed.context_token_cap).toBe(INPUT_LIMITS.CONTEXT_TOKEN_CAP)
  })

  it('rejects one above the bound', () => {
    expect(() =>
      agentCreateSchema.parse({
        ...base,
        context_token_cap: INPUT_LIMITS.CONTEXT_TOKEN_CAP + 1,
      }),
    ).toThrow()
  })

  it('still coerces 0/empty to null (provider default)', () => {
    const parsed = agentCreateSchema.parse({ ...base, context_token_cap: 0 })
    expect(parsed.context_token_cap).toBeNull()
  })
})
