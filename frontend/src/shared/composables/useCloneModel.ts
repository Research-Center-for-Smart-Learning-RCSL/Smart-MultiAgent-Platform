import { reactive, watch, type UnwrapNestedRefs } from 'vue'
import { deepCloneJSON } from '@shared/utils/deepClone'

/**
 * Shared reactive v-model for JSON-safe config objects. A v-model parent hands
 * over a reactive proxy, which `structuredClone` rejects, so clone in and out
 * via JSON round-trip.
 *
 * Tracks the last object this side emitted by reference. When a plain v-model
 * reassignment echoes that exact object back as the new `modelValue` (the
 * common case — no transform in between), the watcher skips re-cloning it into
 * `local`, since `local` already holds that state. This is a reference check,
 * not a time-based flag, so it can never mistake a genuinely different
 * external update (e.g. a concurrent editor session) for our own echo.
 */
export function useCloneModel<T extends object>(
  props: { modelValue: T },
  emit: (event: 'update:modelValue', value: T) => void,
) {
  const local = reactive(deepCloneJSON(props.modelValue)) as UnwrapNestedRefs<T>
  let lastEmitted: T | null = null

  watch(
    () => props.modelValue,
    (v) => {
      if (v === lastEmitted) return
      Object.assign(local, deepCloneJSON(v))
    },
    { deep: true },
  )

  function emitUpdate(): void {
    const next = deepCloneJSON(local as T)
    lastEmitted = next
    emit('update:modelValue', next)
  }

  return { local, emitUpdate }
}
