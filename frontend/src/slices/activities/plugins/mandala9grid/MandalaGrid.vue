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

// Keyed by property name. A boolean cell holds a boolean (it renders as a
// checkbox); every other kind holds the raw string its textarea produced, which
// is what `assemblePayload` expects to convert.
const values = ref<Record<string, string | boolean>>({})
const fieldErrors = ref<Record<string, string>>({})
const submitting = ref(false)

function isCenter(field: SchemaField): boolean {
  return !!centerField.value && field.name === centerField.value.name
}

// Explicit accessors rather than `v-model` on the union-typed record: a textarea's
// model cannot accept a boolean, and the template's v-if/v-else does not narrow an
// indexed access for the type checker.
function textValue(name: string): string {
  const v = values.value[name]
  return typeof v === 'string' ? v : ''
}

function boolValue(name: string): boolean {
  return values.value[name] === true
}

function onText(name: string, event: Event): void {
  values.value[name] = (event.target as HTMLTextAreaElement).value
}

function onCheck(name: string, event: Event): void {
  values.value[name] = (event.target as HTMLInputElement).checked
}

/** The i18n key for a cell's error, or null when it has none.
 *
 *  Since assembly uses the declared kinds, more than one error kind is reachable:
 *  an unparseable JSON cell, and a value the schema rejects. A blank required cell
 *  is the everyday case and gets the specific wording; the rest reuse the generic
 *  form strings rather than mislabelling everything "required". */
function errorKeyFor(field: SchemaField): string | null {
  const err = fieldErrors.value[field.name]
  if (!err) return null
  if (err === 'invalidJson') return 'activities.form.invalidJson'
  if (field.required && !textValue(field.name)) return 'activities.mandala.fieldRequired'
  return 'activities.form.fieldInvalid'
}

async function onSubmit(): Promise<void> {
  if (submitting.value) return
  // Assemble and check against the schema's *declared* kinds. An earlier revision
  // narrowed everything to strings to stop `assemblePayload`'s boolean branch
  // silently submitting `false`; that fixed the silent case but broke the honest
  // ones — a number cell then sent "5" and earned a server 422 the participant
  // could neither see per-field nor fix. Booleans render as a checkbox instead
  // (see the template), so the declared kinds are safe to use and every other type
  // keeps its proper client-side validation.
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
        <!-- The schema's `description` is the author's instruction to the
             participant (the seeded mandala puts its whole prompt there). The
             generic SchemaForm shows it via SFormField's help slot; dropping it
             here would silently lose the one instruction this plugin exists to
             present. -->
        <p
          v-if="field.description"
          :id="`mandala-help-${field.name}`"
          class="text-xs text-[var(--color-muted)]"
        >
          {{ field.description }}
        </p>
        <!-- A checkbox rather than a textarea for boolean properties: a textarea
             cannot express a boolean, and routing typed text through
             assemblePayload's boolean branch submits `false` for anything. -->
        <input
          v-if="field.kind === 'boolean'"
          :id="`mandala-${field.name}`"
          type="checkbox"
          class="h-4 w-4 self-start"
          :checked="boolValue(field.name)"
          :disabled="submitting"
          :aria-invalid="!!fieldErrors[field.name]"
          :aria-describedby="field.description ? `mandala-help-${field.name}` : undefined"
          :data-testid="`mandala-input-${field.name}`"
          @change="onCheck(field.name, $event)"
        >
        <textarea
          v-else
          :id="`mandala-${field.name}`"
          :value="textValue(field.name)"
          rows="3"
          class="w-full resize-y rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-2 text-sm"
          :disabled="submitting"
          :aria-invalid="!!fieldErrors[field.name]"
          :aria-describedby="field.description ? `mandala-help-${field.name}` : undefined"
          :data-testid="`mandala-input-${field.name}`"
          @input="onText(field.name, $event)"
        />
        <p
          v-if="errorKeyFor(field)"
          class="text-xs text-[var(--color-danger)]"
          role="alert"
        >
          {{ props.t(errorKeyFor(field)!) }}
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
