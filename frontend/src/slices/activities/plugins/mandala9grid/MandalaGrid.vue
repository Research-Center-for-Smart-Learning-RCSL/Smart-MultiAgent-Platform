<script setup lang="ts">
// The 3x3 Mandala worksheet. Mounted as a standalone Vue island by the plugin's
// `render` (index.ts), which means this component has NO app context: no
// vue-i18n, no Pinia, no router. Translation arrives as the `t` prop (the SDK
// surfaces `ctx.t` precisely for this), and only plain elements are used — a
// `@shared/ui` component that internally calls `useI18n()` would throw here.

import { computed, ref } from 'vue'

import {
  assemblePayload,
  fieldsFromSchema,
  validatePayload,
  type SchemaField,
} from '../../components/schemaFields'
import type { ActivityTranslate, JSONSchema } from '../../sdk/types'

const props = defineProps<{
  schema: JSONSchema
  t: ActivityTranslate
  submit: (payload: unknown) => Promise<unknown>
}>()

const GRID_SIZE = 9
const CENTER_PROPERTY = 'center'
const CENTER_INDEX = 4

const fields = computed<SchemaField[]>(() => fieldsFromSchema(props.schema))

// A nine-field schema lays out as the classic grid; anything else renders as a
// single column rather than a broken 3x3 — degrade, never drop (R30.18).
const isGrid = computed(() => fields.value.length === GRID_SIZE)

const centerField = computed<SchemaField | null>(() => {
  if (!isGrid.value) return null
  return fields.value.find((f) => f.name === CENTER_PROPERTY) ?? fields.value[0] ?? null
})

/** Ring cells in declaration order with the centre spliced into the middle. */
const cells = computed<SchemaField[]>(() => {
  const center = centerField.value
  if (!center) return []
  const ring = fields.value.filter((f) => f.name !== center.name)
  return [...ring.slice(0, CENTER_INDEX), center, ...ring.slice(CENTER_INDEX)]
})

/** The cells actually rendered, in display order. */
const displayFields = computed<SchemaField[]>(() => (isGrid.value ? cells.value : fields.value))

// Every cell renders as a textarea regardless of the declared property type, so
// the model is uniformly string-valued.
const values = ref<Record<string, string>>({})
const fieldErrors = ref<Record<string, string>>({})
const submitting = ref(false)

function isCenter(field: SchemaField): boolean {
  return !!centerField.value && field.name === centerField.value.name
}

async function onSubmit(): Promise<void> {
  if (submitting.value) return
  // Assemble and check against what was rendered, not what the schema declares.
  // Every cell is a textarea, so every value is a string. Taking `assemblePayload`'s
  // per-kind branch would turn a typed-in boolean property into a submitted `false`
  // — schema-valid, therefore silently persisted in place of the answer — while
  // `validatePayload` against the declared types would reject the string and trap
  // the participant behind an error they cannot clear. Narrowing both to strings
  // keeps the useful client check (blank required cell) and leaves any genuine type
  // mismatch to the server, which is authoritative anyway.
  const asRendered = fields.value.map((f) => ({ ...f, kind: 'string' as const }))
  const renderedSchema: JSONSchema = {
    ...props.schema,
    properties: Object.fromEntries(asRendered.map((f) => [f.name, { type: 'string' as const }])),
  }
  const { payload, fieldErrors: parseErrors } = assemblePayload(asRendered, values.value)
  const errors = { ...parseErrors, ...validatePayload(renderedSchema, payload) }
  fieldErrors.value = errors
  if (Object.keys(errors).length > 0) return

  submitting.value = true
  try {
    await props.submit(payload)
  } catch {
    // The host owns the error message (useActivityHost sets errorMessage, then
    // rethrows). Swallowing here keeps the grid mounted with the answers intact.
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <div
      :class="isGrid ? 'grid grid-cols-1 gap-3 sm:grid-cols-3' : 'flex flex-col gap-3'"
      :data-testid="isGrid ? 'mandala-grid' : 'mandala-list'"
    >
      <div
        v-for="field in displayFields"
        :key="field.name"
        class="flex flex-col gap-1"
        :class="
          isGrid
            ? isCenter(field)
              ? 'rounded-lg border border-[var(--color-accent)] bg-[var(--color-info-tint)] p-2'
              : 'rounded-lg border border-[var(--color-border)] p-2'
            : ''
        "
        :data-testid="isGrid && isCenter(field) ? 'mandala-cell-center' : 'mandala-cell'"
      >
        <label
          :for="`mandala-${field.name}`"
          class="text-xs font-medium text-[var(--color-muted)]"
        >
          {{ field.label }}
        </label>
        <textarea
          :id="`mandala-${field.name}`"
          v-model="values[field.name]"
          rows="3"
          class="w-full resize-y rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-2 text-sm"
          :disabled="submitting"
          :aria-invalid="!!fieldErrors[field.name]"
          :data-testid="`mandala-input-${field.name}`"
        />
        <!-- Every field is narrowed to a string before checking, so the only
             error reachable here is a blank required cell. Revisit this label if
             a new error kind is ever introduced. -->
        <p
          v-if="fieldErrors[field.name]"
          class="text-xs text-[var(--color-danger)]"
          role="alert"
        >
          {{ props.t('activities.mandala.fieldRequired') }}
        </p>
      </div>
    </div>

    <div class="flex justify-end">
      <button
        type="button"
        class="rounded border border-[var(--color-accent)] bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
        :disabled="submitting"
        data-testid="mandala-submit"
        @click="onSubmit"
      >
        {{ props.t('activities.mandala.submit') }}
      </button>
    </div>
  </div>
</template>
