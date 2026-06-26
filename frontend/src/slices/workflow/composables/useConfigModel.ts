import { reactive, watch, type UnwrapNestedRefs } from 'vue'

type Emit = (event: 'update:modelValue', value: Record<string, unknown>) => void

/**
 * Shared reactive model for workflow config forms.
 *
 * Replaces the clone/reactive/watch/update boilerplate that was copy-pasted
 * across every config form component.
 *
 * Returns a reactive `local` object and an `update(field, value)` helper
 * that writes the field and emits the full config object.
 */
// Plain-data deep clone. `structuredClone` throws DataCloneError on a Vue
// reactive proxy, and the modelValue arrives as one (it is read from the
// reactive flowNodes graph and passed down via props). Config is pure JSON,
// so a JSON round-trip is both safe and proxy-tolerant.
function clone(v: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(v)) as Record<string, unknown>
}

export function useConfigModel(
  props: { modelValue: Record<string, unknown> },
  emit: Emit,
) {
  const local: UnwrapNestedRefs<Record<string, unknown>> = reactive(
    clone(props.modelValue),
  )

  watch(
    () => props.modelValue,
    (v) => {
      const fresh = clone(v)
      // Remove keys that no longer exist in the incoming value
      for (const k of Object.keys(local)) {
        if (!(k in fresh)) delete local[k]
      }
      Object.assign(local, fresh)
    },
    { deep: true },
  )

  function update(field: string, value: unknown) {
    local[field] = value
    emit('update:modelValue', { ...local })
  }

  return { local, update }
}

/**
 * Safe number parser for config form inputs.
 *
 * Returns `fallback` when the raw string is empty or non-numeric,
 * preventing Number('') → 0 from bypassing min constraints.
 */
export function safeNumber(raw: string, fallback: number): number {
  if (raw === '' || raw == null) return fallback
  const n = Number(raw)
  return Number.isFinite(n) ? n : fallback
}
