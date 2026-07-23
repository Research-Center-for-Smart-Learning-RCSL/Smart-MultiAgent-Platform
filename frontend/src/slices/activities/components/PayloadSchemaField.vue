<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { SButton, SCodeEditor } from '@shared/ui'

import type { JSONSchema } from '../sdk/types'
import SchemaBuilder from './SchemaBuilder.vue'
import { isFlatSchema } from '../types/schemas'

// Wraps the guided SchemaBuilder and a raw JSON-Schema editor behind a mode
// toggle (FU-5). The builder stays flat scalars; raw JSON is the escape hatch
// for nested/array/enum schemas the builder can't express. Round-trip is
// one-way per the dossier's Q-1(a): the builder can seed the raw editor, but
// once the raw value is edited the builder is locked, since a nested schema
// cannot be represented back in the flat builder without silent loss.
const props = defineProps<{ initial?: JSONSchema | null }>()
const emit = defineEmits<{
  (e: 'update:modelValue', schema: JSONSchema): void
  // i18n message key for a JSON parse error, or null when the raw value parses.
  (e: 'update:parseError', key: string | null): void
}>()

const { t } = useI18n()

const EMPTY_SCHEMA: JSONSchema = { type: 'object', properties: {} }

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

// A schema that isn't flat-representable (nested/array/enum, or extra per-property
// keys the builder would drop) must open in raw mode with the builder locked, so
// toggling to the builder can never silently discard structure. `isFlatSchema`
// is the single source for that rule (shared with the builder's field types).
const startInRaw = !!props.initial && !isFlatSchema(props.initial)

const mode = ref<'builder' | 'raw'>(startInRaw ? 'raw' : 'builder')
// Locked once the raw value has been edited (Q-1a); starts locked when the
// initial schema was too rich for the builder to represent.
const builderLocked = ref(startInRaw)
const currentSchema = ref<JSONSchema>(props.initial ?? EMPTY_SCHEMA)
const rawText = ref(startInRaw ? JSON.stringify(currentSchema.value, null, 2) : '')

onMounted(() => {
  // The builder is not mounted in raw mode, so seed the parent ourselves.
  if (mode.value === 'raw') emit('update:modelValue', currentSchema.value)
})

function onBuilderUpdate(schema: JSONSchema): void {
  currentSchema.value = schema
  emit('update:modelValue', schema)
}

function switchToRaw(): void {
  if (mode.value === 'raw') return
  rawText.value = JSON.stringify(currentSchema.value, null, 2)
  emit('update:parseError', null)
  mode.value = 'raw'
}

function switchToBuilder(): void {
  if (mode.value === 'builder' || builderLocked.value) return
  emit('update:parseError', null)
  mode.value = 'builder'
}

function onRawUpdate(text: string): void {
  rawText.value = text
  builderLocked.value = true
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    emit('update:parseError', 'schemaInvalidJson')
    // An empty-properties object fails the form's own ">= 1 property" check, so
    // submit is blocked while the raw value is unparseable.
    emit('update:modelValue', EMPTY_SCHEMA)
    return
  }
  // Valid JSON that isn't an object (array, number, string, null) can't be a
  // payload schema; flag it distinctly rather than emit a non-object up (which
  // would surface the misleading empty-schema message) or break the emit
  // contract by passing null.
  if (!isPlainObject(parsed)) {
    emit('update:parseError', 'schemaNotObject')
    emit('update:modelValue', EMPTY_SCHEMA)
    return
  }
  emit('update:parseError', null)
  emit('update:modelValue', parsed as JSONSchema)
}

const modes = computed(() => [
  { value: 'builder' as const, label: t('activities.typeForm.schemaMode.builder') },
  { value: 'raw' as const, label: t('activities.typeForm.schemaMode.raw') },
])
</script>

<template>
  <div class="flex flex-col gap-3">
    <div
      class="inline-flex self-start rounded-md border border-[var(--color-border)] p-0.5"
      role="group"
      :aria-label="t('activities.typeForm.schemaModeLabel')"
    >
      <SButton
        v-for="m in modes"
        :key="m.value"
        size="sm"
        :variant="mode === m.value ? 'primary' : 'ghost'"
        :disabled="m.value === 'builder' && builderLocked"
        :title="m.value === 'builder' && builderLocked
          ? t('activities.typeForm.schemaModeLockedHint')
          : undefined"
        :aria-pressed="mode === m.value"
        :data-testid="`schema-mode-${m.value}`"
        @click="m.value === 'raw' ? switchToRaw() : switchToBuilder()"
      >
        {{ m.label }}
      </SButton>
    </div>

    <SchemaBuilder
      v-if="mode === 'builder'"
      :initial="currentSchema"
      @update:model-value="onBuilderUpdate"
    />

    <template v-else>
      <p class="text-xs text-[var(--color-muted)]">
        {{ t('activities.typeForm.schemaRawHint') }}
      </p>
      <SCodeEditor
        :model-value="rawText"
        language="json"
        :placeholder="t('activities.typeForm.schemaRawPlaceholder')"
        data-testid="schema-raw-editor"
        @update:model-value="onRawUpdate"
      />
    </template>
  </div>
</template>
