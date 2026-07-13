// Pure helpers behind SchemaForm.vue — kept separate so field derivation,
// payload assembly, and client-side (UX-only) Zod validation are unit-testable
// without mounting. Scope is deliberately capped (§5.3): string, number,
// boolean, enum, array-of-enum. Anything else degrades to a labelled JSON
// textarea — a field is never silently dropped.

import { z, type ZodTypeAny } from 'zod'
import type { JSONSchema } from '../sdk/types'

export type SchemaFieldKind = 'string' | 'number' | 'boolean' | 'enum' | 'enum-array' | 'json'

export interface SchemaField {
  name: string
  kind: SchemaFieldKind
  label: string
  required: boolean
  options: string[]
  description: string | null
}

function labelFor(name: string, node: JSONSchema): string {
  return typeof node.title === 'string' && node.title ? node.title : name
}

function kindFor(node: JSONSchema): SchemaFieldKind {
  if (Array.isArray(node.enum)) return 'enum'
  switch (node.type) {
    case 'string':
      return 'string'
    case 'number':
    case 'integer':
      return 'number'
    case 'boolean':
      return 'boolean'
    case 'array': {
      const items = node.items
      if (items && Array.isArray(items.enum)) return 'enum-array'
      return 'json'
    }
    default:
      return 'json'
  }
}

export function fieldsFromSchema(schema: JSONSchema | null | undefined): SchemaField[] {
  if (!schema || !schema.properties) return []
  const required = new Set(Array.isArray(schema.required) ? schema.required : [])
  return Object.entries(schema.properties).map(([name, node]) => {
    const kind = kindFor(node)
    const enumSource =
      kind === 'enum' ? node.enum : kind === 'enum-array' ? node.items?.enum : null
    return {
      name,
      kind,
      label: labelFor(name, node),
      required: required.has(name),
      options: Array.isArray(enumSource) ? enumSource.map((v) => String(v)) : [],
      description: typeof node.description === 'string' ? node.description : null,
    }
  })
}

export function initialValues(fields: SchemaField[]): Record<string, unknown> {
  const values: Record<string, unknown> = {}
  for (const f of fields) {
    switch (f.kind) {
      case 'boolean':
        values[f.name] = false
        break
      case 'enum':
      case 'number':
        values[f.name] = null
        break
      case 'enum-array':
        values[f.name] = []
        break
      case 'string':
      case 'json':
        values[f.name] = ''
        break
    }
  }
  return values
}

export interface AssembleResult {
  payload: Record<string, unknown>
  /** JSON parse errors keyed by field name; value is an i18n key suffix. */
  fieldErrors: Record<string, string>
}

/** Turn the raw input model into the payload object to submit. Empty optional
 *  values are omitted; a JSON field with unparseable text yields a field error. */
export function assemblePayload(
  fields: SchemaField[],
  raw: Record<string, unknown>,
): AssembleResult {
  const payload: Record<string, unknown> = {}
  const fieldErrors: Record<string, string> = {}
  for (const f of fields) {
    const v = raw[f.name]
    switch (f.kind) {
      case 'boolean':
        payload[f.name] = v === true
        break
      case 'number':
        if (v !== null && v !== '' && v !== undefined && !Number.isNaN(Number(v))) {
          payload[f.name] = Number(v)
        }
        break
      case 'enum':
        if (v !== null && v !== undefined && v !== '') payload[f.name] = v
        break
      case 'enum-array':
        payload[f.name] = Array.isArray(v) ? v : []
        break
      case 'string':
        payload[f.name] = typeof v === 'string' ? v : ''
        break
      case 'json': {
        const text = typeof v === 'string' ? v.trim() : ''
        if (text) {
          try {
            payload[f.name] = JSON.parse(text)
          } catch {
            fieldErrors[f.name] = 'invalidJson'
          }
        }
        break
      }
    }
  }
  return { payload, fieldErrors }
}

function zodForField(f: SchemaField): ZodTypeAny {
  let field: ZodTypeAny
  switch (f.kind) {
    case 'string':
      field = f.required ? z.string().min(1) : z.string()
      break
    case 'number':
      field = z.number()
      break
    case 'boolean':
      field = z.boolean()
      break
    case 'enum':
      field = f.options.length ? z.enum(f.options as [string, ...string[]]) : z.string()
      break
    case 'enum-array': {
      const el = f.options.length ? z.enum(f.options as [string, ...string[]]) : z.string()
      field = f.required ? z.array(el).min(1) : z.array(el)
      break
    }
    case 'json':
      field = z.unknown()
      break
  }
  return f.required ? field : field.optional()
}

export function jsonSchemaToZod(
  schema: JSONSchema | null | undefined,
): z.ZodType<Record<string, unknown>> {
  const shape: Record<string, ZodTypeAny> = {}
  for (const f of fieldsFromSchema(schema)) shape[f.name] = zodForField(f)
  return z.object(shape).passthrough()
}

/** Client-side (UX-only) validation. Returns a per-field i18n key suffix; the
 *  server re-validates authoritatively regardless of this result. */
export function validatePayload(
  schema: JSONSchema | null | undefined,
  payload: Record<string, unknown>,
): Record<string, string> {
  const result = jsonSchemaToZod(schema).safeParse(payload)
  if (result.success) return {}
  const errors: Record<string, string> = {}
  for (const issue of result.error.issues) {
    const key = String(issue.path[0] ?? '')
    if (key && !errors[key]) errors[key] = 'fieldInvalid'
  }
  return errors
}
