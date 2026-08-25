// Client-side shape for the activity-type authoring form. Mirrors backend
// `ActivityTypeIn` (backend/app/api/v1/activities.py) after `assembleBody`
// folds the flat validator fields into `validator_config`. The server
// re-validates the payload schema and validator config authoritatively; this
// schema only gates the form for immediate feedback.

import { z } from 'zod'

import type { JSONSchema } from '../sdk/types'

// The validator kinds the authoring form can offer. `in_process` is gated at
// runtime: the form drops it unless the platform reports ≥1 registered first-party
// validator (GET /api/activity-validators), so it never appears with an empty picker.
export const VALIDATOR_KINDS = ['webhook', 'mcp', 'in_process'] as const
export type ValidatorKindOption = (typeof VALIDATOR_KINDS)[number]

// The first-party validators that have a per-validator sub-form. `exact_match`
// collects `field` + `expected`; `filled_count` collects `min_filled`. Any other
// registered id folds to just `{validator_id}`. `GET /api/activity-validators`
// returns only `{id, title}` — no config schema — so each validator's sub-form is
// necessarily hand-written here.
export const EXACT_MATCH_VALIDATOR_ID = 'exact_match'
export const FILLED_COUNT_VALIDATOR_ID = 'filled_count'
// Same `min_filled` config contract as `filled_count`, so it shares that
// sub-form. Without this the picker would offer a validator whose only legal
// config the form cannot produce, and the backend would 422 every save.
export const FILLED_COUNT_COVERAGE_VALIDATOR_ID = 'filled_count_coverage'

export function usesMinFilled(validatorId: string): boolean {
  return (
    validatorId === FILLED_COUNT_VALIDATOR_ID || validatorId === FILLED_COUNT_COVERAGE_VALIDATOR_ID
  )
}

// The flat scalar field types the guided builder can author. Nested objects and
// arrays are the raw-JSON escape hatch's job (FU-5), not the builder's.
export const SCHEMA_FIELD_TYPES = ['string', 'number', 'integer', 'boolean'] as const
export type SchemaFieldType = (typeof SCHEMA_FIELD_TYPES)[number]

// Whether a payload schema round-trips through the flat SchemaBuilder without
// loss: an object whose every property is a bare scalar `{ type }` whose type is
// one of SCHEMA_FIELD_TYPES, and which carries no top-level key the builder would
// drop. The raw-JSON editor (FU-5) uses this to decide whether a stored schema
// can open in the guided builder or must stay in the raw editor. Kept here beside
// SCHEMA_FIELD_TYPES so the definition of "what the flat builder can express"
// lives in one place.
export function isFlatSchema(schema: JSONSchema | null | undefined): boolean {
  if (!schema || schema.type !== 'object') return false
  const allowedTop = new Set(['type', 'properties', 'required'])
  if (Object.keys(schema).some((k) => !allowedTop.has(k))) return false
  const properties = schema.properties
  if (!properties) return false
  const known = new Set<string>(SCHEMA_FIELD_TYPES)
  return Object.values(properties).every((prop) => {
    if (!prop || typeof prop !== 'object') return false
    const keys = Object.keys(prop)
    return keys.length === 1 && keys[0] === 'type' && known.has((prop as JSONSchema).type as string)
  })
}

const emptyToNull = (v: unknown): unknown =>
  v === '' || v === 0 || v === null || v === undefined ? null : v

// Deliberately NOT `emptyToNull`: that one folds 0 to null, which would destroy
// `min_filled: 0` — the legal threshold that makes an activity collect-only.
// `undefined` is left alone so the wrapped `.default(0)` still applies.
const blankToNull = (v: unknown): unknown => (v === '' || v === null ? null : v)

// The builder emits `{type:'object', properties, required?}`. Require at least
// one property so a type is never registered with an empty schema.
const payloadSchema = z.custom<JSONSchema>().refine(
  (s) =>
    !!s &&
    s.type === 'object' &&
    !!s.properties &&
    Object.keys(s.properties).length > 0,
  { message: 'schemaEmpty' },
)

export const activityTypeCreateSchema = z
  .object({
    key: z.string().trim().min(1).max(128),
    name: z.string().trim().min(1).max(256),
    retention_days: z.preprocess(
      emptyToNull,
      z.number().int().min(1).nullable().default(null),
    ),
    expose_payload_to_agent: z.boolean().default(true),
    echo_includes_content: z.boolean().default(false),
    payload_schema: payloadSchema,
    validator_kind: z.enum(VALIDATOR_KINDS),
    // Validator sub-form fields — validated conditionally below and folded into
    // `validator_config` by `assembleValidatorConfig` at submit.
    webhook_url: z.string().trim().default(''),
    mcp_agent_id: z.string().trim().default(''),
    mcp_binding_id: z.string().trim().default(''),
    mcp_tool_name: z.string().trim().default(''),
    // in_process sub-form. `expected` stays a string in the form and is coerced to
    // the selected field's schema type by `assembleValidatorConfig` at submit.
    in_process_validator_id: z.string().trim().default(''),
    exact_match_field: z.string().trim().default(''),
    exact_match_expected: z.string().default(''),
    exact_match_case_sensitive: z.boolean().default(false),
    // filled_count sub-form. 0 is a legal, meaningful value (collect-only), so it
    // must survive preprocessing — see `blankToNull`.
    filled_count_min: z.preprocess(
      blankToNull,
      z.coerce.number().int().min(0).nullable().default(0),
    ),
  })
  .superRefine((val, ctx) => {
    if (val.validator_kind === 'webhook') {
      if (!val.webhook_url) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['webhook_url'], message: 'required' })
      }
    } else if (val.validator_kind === 'mcp') {
      for (const f of ['mcp_agent_id', 'mcp_binding_id', 'mcp_tool_name'] as const) {
        if (!val[f]) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: [f], message: 'required' })
        }
      }
    } else if (val.validator_kind === 'in_process') {
      if (!val.in_process_validator_id) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['in_process_validator_id'],
          message: 'required',
        })
      } else if (val.in_process_validator_id === EXACT_MATCH_VALIDATOR_ID) {
        if (!val.exact_match_field) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['exact_match_field'], message: 'required' })
        }
        if (val.exact_match_expected === '') {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['exact_match_expected'],
            message: 'required',
          })
        }
      } else if (usesMinFilled(val.in_process_validator_id)) {
        const min = val.filled_count_min
        if (min === null || !Number.isInteger(min) || min < 0) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['filled_count_min'],
            message: 'required',
          })
        } else if (min > Object.keys(val.payload_schema?.properties ?? {}).length) {
          // The scorer counts declared properties only, so a threshold above the
          // field count can never be met: every submission would come back
          // invalid with nothing the participant could do about it. The backend
          // config validator cannot catch this — it only receives the config, not
          // the schema — so this check has to live here.
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['filled_count_min'],
            message: 'exceedsFieldCount',
          })
        }
      }
    }
  })

export type ActivityTypeCreateInput = z.infer<typeof activityTypeCreateSchema>

export function assembleValidatorConfig(
  values: ActivityTypeCreateInput,
): Record<string, unknown> {
  if (values.validator_kind === 'webhook') {
    return { url: values.webhook_url }
  }
  if (values.validator_kind === 'in_process') {
    const config: Record<string, unknown> = { validator_id: values.in_process_validator_id }
    if (values.in_process_validator_id === EXACT_MATCH_VALIDATOR_ID) {
      config.field = values.exact_match_field
      config.expected = coerceExpected(
        values.exact_match_expected,
        schemaFieldType(values.payload_schema, values.exact_match_field),
      )
      config.case_sensitive = values.exact_match_case_sensitive
    } else if (usesMinFilled(values.in_process_validator_id)) {
      config.min_filled = values.filled_count_min ?? 0
    }
    return config
  }
  return {
    agent_id: values.mcp_agent_id,
    binding_id: values.mcp_binding_id,
    tool_name: values.mcp_tool_name,
  }
}

// The declared type of one payload-schema property, so `expected` submits as the
// same JSON type the payload will carry (a numeric answer compares equal to a
// numeric submission, not to its string form).
function schemaFieldType(schema: JSONSchema, field: string): string | undefined {
  const props = (schema?.properties ?? {}) as Record<string, { type?: string }>
  return props[field]?.type
}

function coerceExpected(raw: string, type: string | undefined): unknown {
  if (type === 'number' || type === 'integer') {
    const n = Number(raw)
    return raw.trim() !== '' && !Number.isNaN(n) ? n : raw
  }
  if (type === 'boolean') {
    return raw === 'true'
  }
  return raw
}
