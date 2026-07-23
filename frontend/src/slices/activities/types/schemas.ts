// Client-side shape for the activity-type authoring form. Mirrors backend
// `ActivityTypeIn` (backend/app/api/v1/activities.py) after `assembleBody`
// folds the flat validator fields into `validator_config`. The server
// re-validates the payload schema and validator config authoritatively; this
// schema only gates the form for immediate feedback.

import { z } from 'zod'

import type { JSONSchema } from '../sdk/types'

// v1 offers only the validator kinds that dispatch and score end-to-end today.
// `in_process` is deliberately absent (no first-party validators are registered
// on the platform) — see the dossier's FU-1.
export const VALIDATOR_KINDS = ['webhook', 'mcp'] as const
export type ValidatorKindOption = (typeof VALIDATOR_KINDS)[number]

// The flat scalar field types the guided builder can author. Nested objects and
// arrays are the raw-JSON escape hatch's job (FU-5), not the builder's.
export const SCHEMA_FIELD_TYPES = ['string', 'number', 'integer', 'boolean'] as const
export type SchemaFieldType = (typeof SCHEMA_FIELD_TYPES)[number]

const emptyToNull = (v: unknown): unknown =>
  v === '' || v === 0 || v === null || v === undefined ? null : v

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
    payload_schema: payloadSchema,
    validator_kind: z.enum(VALIDATOR_KINDS),
    // Validator sub-form fields — validated conditionally below and folded into
    // `validator_config` by `assembleValidatorConfig` at submit.
    webhook_url: z.string().trim().default(''),
    mcp_agent_id: z.string().trim().default(''),
    mcp_binding_id: z.string().trim().default(''),
    mcp_tool_name: z.string().trim().default(''),
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
    }
  })

export type ActivityTypeCreateInput = z.infer<typeof activityTypeCreateSchema>

export function assembleValidatorConfig(
  values: ActivityTypeCreateInput,
): Record<string, unknown> {
  if (values.validator_kind === 'webhook') {
    return { url: values.webhook_url }
  }
  return {
    agent_id: values.mcp_agent_id,
    binding_id: values.mcp_binding_id,
    tool_name: values.mcp_tool_name,
  }
}
