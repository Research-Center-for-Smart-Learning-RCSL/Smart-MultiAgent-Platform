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

// Every cell renders as a textarea regardless of the declared property type, so
// the model is uniformly string-valued; `assemblePayload` converts back to the
// schema's types at submit.
const values = ref<Record<string, string>>({})
const fieldErrors = ref<Record<string, string>>({})
const submitting = ref(false)

function isCenter(field: SchemaField): boolean {
  return !!centerField.value && field.name === centerField.value.name
}

async function onSubmit(): Promise<void> {
  if (submitting.value) return
  const { payload, fieldErrors: parseErrors } = assemblePayload(fields.value, values.value)
  const errors = { ...parseErrors, ...validatePayload(props.schema, payload) }
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
      v-if="isGrid"
      class="grid grid-cols-1 gap-3 sm:grid-cols-3"
      data-testid="mandala-grid"
    >
      <div
        v-for="field in cells"
        :key="field.name"
        class="flex flex-col gap-1 rounded-lg border p-2"
        :class="
          isCenter(field)
            ? 'border-[var(--color-accent)] bg-[var(--color-info-tint)]'
            : 'border-[var(--color-border)]'
        "
        :data-testid="isCenter(field) ? 'mandala-cell-center' : 'mandala-cell'"
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
          :data-testid="`mandala-input-${field.name}`"
        />
        <p
          v-if="fieldErrors[field.name]"
          class="text-xs text-[var(--color-danger)]"
          role="alert"
        >
          {{ props.t('activities.mandala.fieldRequired') }}
        </p>
      </div>
    </div>

    <div
      v-else
      class="flex flex-col gap-3"
      data-testid="mandala-list"
    >
      <div
        v-for="field in fields"
        :key="field.name"
        class="flex flex-col gap-1"
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
          :data-testid="`mandala-input-${field.name}`"
        />
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
