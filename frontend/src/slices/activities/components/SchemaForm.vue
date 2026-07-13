<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SButton, SCheckbox, SFormField, SInput, SSelect, STextarea } from '@shared/ui'
import type { JSONSchema } from '../sdk/types'
import {
  assemblePayload,
  fieldsFromSchema,
  initialValues,
  validatePayload,
  type SchemaField,
} from './schemaFields'

const props = withDefaults(
  defineProps<{
    schema: JSONSchema | null
    submitting?: boolean
    disabled?: boolean
  }>(),
  { submitting: false, disabled: false },
)

const emit = defineEmits<{
  submit: [payload: Record<string, unknown>]
}>()

const { t } = useI18n()

const fields = computed<SchemaField[]>(() => fieldsFromSchema(props.schema))

// Raw input model. Rebuilt whenever the schema changes so no stale field lingers.
const values = reactive<Record<string, unknown>>({})
const errors = reactive<Record<string, string>>({})

watch(
  fields,
  (next) => {
    for (const k of Object.keys(values)) delete values[k]
    for (const k of Object.keys(errors)) delete errors[k]
    Object.assign(values, initialValues(next))
  },
  { immediate: true },
)

function strVal(name: string): string | number {
  const v = values[name]
  return typeof v === 'string' || typeof v === 'number' ? v : ''
}
function enumVal(name: string): string | number | null {
  const v = values[name]
  return typeof v === 'string' || typeof v === 'number' ? v : null
}
function arrHas(name: string, option: string | number): boolean {
  const v = values[name]
  return Array.isArray(v) && v.includes(option)
}
function boolVal(name: string): boolean {
  return values[name] === true
}
function errorText(name: string): string {
  const key = errors[name]
  return key ? t(key) : ''
}

function toggleArrayOption(name: string, option: string | number, checked: boolean): void {
  const current = Array.isArray(values[name]) ? (values[name] as Array<string | number>) : []
  values[name] = checked ? [...current, option] : current.filter((o) => o !== option)
}

function onSubmit(): void {
  for (const k of Object.keys(errors)) delete errors[k]
  const { payload, fieldErrors } = assemblePayload(fields.value, values)
  Object.assign(errors, mapKeys(fieldErrors))
  if (Object.keys(fieldErrors).length > 0) return
  const validationErrors = validatePayload(props.schema, payload)
  if (Object.keys(validationErrors).length > 0) {
    Object.assign(errors, mapKeys(validationErrors))
    return
  }
  emit('submit', payload)
}

// The helpers return i18n key suffixes ('invalidJson' | 'fieldInvalid'); resolve
// them to the messages here so the pure module stays i18n-free.
function mapKeys(raw: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, suffix] of Object.entries(raw)) out[k] = `activities.form.${suffix}`
  return out
}
</script>

<template>
  <form
    class="schema-form"
    @submit.prevent="onSubmit"
  >
    <SFormField
      v-for="field in fields"
      :key="field.name"
      :label="field.label"
      :name="field.name"
      :required="field.required"
      :error="errorText(field.name)"
      :help="field.description ?? ''"
    >
      <SInput
        v-if="field.kind === 'string'"
        :model-value="strVal(field.name)"
        :disabled="props.disabled"
        :error="!!errors[field.name]"
        @update:model-value="values[field.name] = $event"
      />
      <!-- Rendered as a text input (not type="number"): SInput coerces an empty
           numeric input to 0, which would silently submit 0 for a cleared field.
           A text input keeps "cleared" as '', which assemblePayload omits and
           parses to a number on submit. -->
      <SInput
        v-else-if="field.kind === 'number'"
        :model-value="strVal(field.name)"
        :disabled="props.disabled"
        :error="!!errors[field.name]"
        @update:model-value="values[field.name] = $event"
      />
      <SCheckbox
        v-else-if="field.kind === 'boolean'"
        :model-value="boolVal(field.name)"
        :disabled="props.disabled"
        @update:model-value="values[field.name] = $event"
      >
        {{ field.label }}
      </SCheckbox>
      <SSelect
        v-else-if="field.kind === 'enum'"
        :model-value="enumVal(field.name)"
        :options="field.options.map((o) => ({ value: o, label: String(o) }))"
        :placeholder="$t('activities.form.selectPlaceholder')"
        :disabled="props.disabled"
        :error="!!errors[field.name]"
        @update:model-value="values[field.name] = $event"
      />
      <div
        v-else-if="field.kind === 'enum-array'"
        class="schema-form__group"
      >
        <SCheckbox
          v-for="option in field.options"
          :key="option"
          :model-value="arrHas(field.name, option)"
          :disabled="props.disabled"
          @update:model-value="toggleArrayOption(field.name, option, $event)"
        >
          {{ option }}
        </SCheckbox>
      </div>
      <STextarea
        v-else
        mono
        :rows="4"
        :model-value="strVal(field.name).toString()"
        :placeholder="$t('activities.form.jsonPlaceholder')"
        :disabled="props.disabled"
        :error="!!errors[field.name]"
        @update:model-value="values[field.name] = $event"
      />
    </SFormField>

    <div class="schema-form__actions">
      <SButton
        type="submit"
        variant="primary"
        :disabled="props.disabled || props.submitting"
        :loading="props.submitting"
      >
        {{ $t('activities.form.submit') }}
      </SButton>
    </div>
  </form>
</template>

<style scoped>
.schema-form__group {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.schema-form__actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 0.5rem;
}
</style>
