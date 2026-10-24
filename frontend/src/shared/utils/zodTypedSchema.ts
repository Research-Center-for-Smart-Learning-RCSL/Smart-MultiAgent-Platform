import {
  cleanupNonNestedPath,
  isNotNestedPath,
  type TypedSchema,
  type TypedSchemaError,
} from 'vee-validate'
import { z } from 'zod'

// Stands in for @vee-validate/zod, whose 4.x line peers on zod 3, so that the
// app can run zod 4 (CVE-2023-54404 is fixed only in zod >= 4.6.6) on the
// stable vee-validate 4. Behaviour follows that package: parse maps issues to
// vee-validate paths, cast falls back to the object's declared defaults, and
// describe reports whether a path is required.

type Issue = z.core.$ZodIssue

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const proto: unknown = Object.getPrototypeOf(value)
  return proto === null || proto === Object.prototype
}

function merge(target: Record<string, unknown>, source: Record<string, unknown>): Record<string, unknown> {
  for (const key of Object.keys(source)) {
    const s = source[key]
    const t = target[key]
    if (isPlainObject(s) && isPlainObject(t)) {
      merge(t, s)
      continue
    }
    target[key] = s
  }
  return target
}

// vee-validate addresses array items as `items[0].name`, zod reports ['items', 0, 'name'].
function toFormPath(path: readonly PropertyKey[]): string {
  let out = ''
  for (const seg of path) {
    const s = String(seg)
    if (typeof seg === 'number' || /^\d+$/.test(s)) {
      out += `[${s}]`
    } else {
      out += out ? `.${s}` : s
    }
  }
  return out
}

function collectIssues(issues: readonly Issue[], errors: Record<string, TypedSchemaError>): void {
  for (const issue of issues) {
    const path = toFormPath(issue.path)
    if (issue.code === 'invalid_union') {
      collectIssues(issue.errors.flat(), errors)
      if (!path) continue
    }
    errors[path] ??= { path, errors: [] }
    errors[path].errors.push(issue.message)
  }
}

function getDefaults(schema: z.ZodType): Record<string, unknown> | undefined {
  if (!(schema instanceof z.ZodObject)) return undefined
  const shape = schema.shape as Record<string, z.ZodType>
  return Object.fromEntries(
    Object.entries(shape).map(([key, value]) => {
      if (value instanceof z.ZodDefault) return [key, value.def.defaultValue]
      if (value instanceof z.ZodObject) return [key, getDefaults(value)]
      return [key, undefined]
    }),
  )
}

function schemaForPath(path: string, schema: z.ZodType): z.ZodType | null {
  if (!(schema instanceof z.ZodObject)) return null
  const shape = schema.shape as Record<string, z.ZodType | undefined>
  if (isNotNestedPath(path)) return shape[cleanupNonNestedPath(path)] ?? null

  let current: z.ZodType | null = schema
  for (const seg of path.split(/\.|\[(\d+)\]/).filter(Boolean)) {
    if (current instanceof z.ZodObject) {
      current = (current.shape as Record<string, z.ZodType | undefined>)[seg] ?? null
    } else if (current instanceof z.ZodArray && /^\d+$/.test(seg)) {
      current = current.element as z.ZodType
    } else {
      return null
    }
    if (!current) return null
  }
  return current
}

export function toTypedSchema<TSchema extends z.ZodType>(
  schema: TSchema,
): TypedSchema<z.input<TSchema>, z.output<TSchema>> {
  return {
    __type: 'VVTypedSchema',
    async parse(value) {
      const result = await schema.safeParseAsync(value)
      if (result.success) return { value: result.data, errors: [] }
      const errors: Record<string, TypedSchemaError> = {}
      collectIssues(result.error.issues, errors)
      return { errors: Object.values(errors) }
    },
    cast(values) {
      try {
        return schema.parse(values) as z.input<TSchema>
      } catch {
        // Zod cannot cast without validating, so fill in declared defaults instead.
        const defaults = getDefaults(schema)
        if (defaults && isPlainObject(values)) return merge(defaults, values) as z.input<TSchema>
        return values as z.input<TSchema>
      }
    },
    describe(path) {
      if (!path) return { required: !schema.safeParse(undefined).success, exists: true }
      const sub = schemaForPath(path, schema)
      if (!sub) return { required: false, exists: false }
      return { required: !sub.safeParse(undefined).success, exists: true }
    },
  }
}
