<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { PlusIcon, TrashIcon } from '@heroicons/vue/24/outline'

import { SButton, SInput, SSelect, SToggle } from '@shared/ui'

import type { JSONSchema } from '../sdk/types'
import { SCHEMA_FIELD_TYPES, type SchemaFieldType } from '../types/schemas'

interface FieldRow {
  name: string
  type: SchemaFieldType
  required: boolean
}

// One-way builder: rows are the source of truth, the assembled JSON Schema is
// emitted upward. Authoring starts empty (create-only), so there is no need to
// rehydrate rows from an incoming schema.
const emit = defineEmits<{ (e: 'update:modelValue', schema: JSONSchema): void }>()

const { t } = useI18n()

const rows = ref<FieldRow[]>([{ name: '', type: 'string', required: true }])

const typeOptions = computed(() =>
  SCHEMA_FIELD_TYPES.map((ty) => ({
    value: ty,
    label: t(`activities.schemaBuilder.fieldType.${ty}`),
  })),
)

// A later duplicate name would silently overwrite the earlier property in the
// emitted object; surface it so the author sees why a field is missing.
const duplicateNames = computed(() => {
  const seen = new Set<string>()
  const dup = new Set<string>()
  for (const r of rows.value) {
    const n = r.name.trim()
    if (!n) continue
    if (seen.has(n)) dup.add(n)
    else seen.add(n)
  }
  return dup
})

const schema = computed<JSONSchema>(() => {
  const properties: Record<string, JSONSchema> = {}
  const required: string[] = []
  for (const r of rows.value) {
    const name = r.name.trim()
    if (!name) continue
    properties[name] = { type: r.type }
    if (r.required) required.push(name)
  }
  const out: JSONSchema = { type: 'object', properties }
  if (required.length) out.required = required
  return out
})

watch(schema, (s) => emit('update:modelValue', s), { immediate: true })

const preview = computed(() => JSON.stringify(schema.value, null, 2))

function addRow(): void {
  rows.value.push({ name: '', type: 'string', required: false })
}

function removeRow(index: number): void {
  rows.value.splice(index, 1)
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div
      v-for="(row, index) in rows"
      :key="index"
      class="flex items-start gap-2"
    >
      <div class="flex-1">
        <SInput
          v-model="row.name"
          :placeholder="t('activities.schemaBuilder.fieldName')"
          :error="duplicateNames.has(row.name.trim())"
          data-testid="schema-field-name"
        />
      </div>
      <div class="w-36">
        <SSelect
          v-model="row.type"
          :options="typeOptions"
        />
      </div>
      <div class="flex items-center gap-1 pt-2">
        <span class="text-xs text-[var(--color-muted)]">
          {{ t('activities.schemaBuilder.required') }}
        </span>
        <SToggle
          v-model="row.required"
          :aria-label="t('activities.schemaBuilder.required')"
        />
      </div>
      <SButton
        variant="ghost"
        icon-only
        size="sm"
        :disabled="rows.length <= 1"
        :aria-label="t('activities.schemaBuilder.removeField')"
        @click="removeRow(index)"
      >
        <TrashIcon class="w-4 h-4" />
      </SButton>
    </div>

    <div>
      <SButton
        variant="secondary"
        size="sm"
        @click="addRow"
      >
        <template #icon-left>
          <PlusIcon class="w-4 h-4" />
        </template>
        {{ t('activities.schemaBuilder.addField') }}
      </SButton>
    </div>

    <div>
      <p class="text-xs text-[var(--color-muted)] mb-1">
        {{ t('activities.schemaBuilder.previewLabel') }}
      </p>
      <pre
        class="text-xs font-mono bg-[var(--color-surface-sunken)] rounded p-3 overflow-x-auto"
        data-testid="schema-preview"
      >{{ preview }}</pre>
    </div>
  </div>
</template>
