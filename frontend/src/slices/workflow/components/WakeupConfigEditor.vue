<script setup lang="ts">
// Wake-up config editor in agent binding panel (G.10).
// Renders the live wakeup_config and allows editing designer-level fields.

import { computed, reactive, watch } from 'vue'
import type { WakeupConfig } from '../types'

const props = defineProps<{
  modelValue: WakeupConfig
  readonly?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: WakeupConfig): void
}>()

const local = reactive<WakeupConfig>(structuredClone(props.modelValue))

watch(() => props.modelValue, (v) => {
  Object.assign(local, structuredClone(v))
}, { deep: true })

function emitUpdate(): void {
  emit('update:modelValue', structuredClone(local))
}

const isInert = computed(() =>
  !local.triggers.every_n_messages.enabled
  && !local.triggers.silence_minutes.enabled
  && !local.triggers.call_only.enabled
)
</script>

<template>
  <div class="wakeup-editor space-y-3 text-sm">
    <div v-if="isInert" class="text-xs text-yellow-600 bg-yellow-50 px-2 py-1 rounded">
      All triggers disabled — agent is inert.
    </div>

    <!-- every_n_messages -->
    <fieldset class="border rounded p-2">
      <legend class="text-xs font-medium px-1">every_n_messages</legend>
      <label class="flex items-center gap-2 mb-1">
        <input
          v-model="local.triggers.every_n_messages.enabled"
          type="checkbox"
          :disabled="readonly"
          @change="emitUpdate"
        />
        <span class="text-xs">Enabled</span>
      </label>
      <label class="flex items-center gap-2">
        <span class="text-xs w-8">n:</span>
        <input
          v-model.number="local.triggers.every_n_messages.n"
          type="number"
          min="1"
          max="1000"
          class="border rounded px-1 py-0.5 w-20 text-xs"
          :disabled="readonly || !local.triggers.every_n_messages.enabled"
          @change="emitUpdate"
        />
      </label>
    </fieldset>

    <!-- silence_minutes -->
    <fieldset class="border rounded p-2">
      <legend class="text-xs font-medium px-1">silence_minutes</legend>
      <label class="flex items-center gap-2 mb-1">
        <input
          v-model="local.triggers.silence_minutes.enabled"
          type="checkbox"
          :disabled="readonly"
          @change="emitUpdate"
        />
        <span class="text-xs">Enabled</span>
      </label>
      <label class="flex items-center gap-2 mb-1">
        <span class="text-xs w-20">t_minutes:</span>
        <input
          v-model.number="local.triggers.silence_minutes.t_minutes"
          type="number"
          min="1"
          max="1440"
          class="border rounded px-1 py-0.5 w-20 text-xs"
          :disabled="readonly || !local.triggers.silence_minutes.enabled"
          @change="emitUpdate"
        />
      </label>
      <label class="flex items-center gap-2 mb-1">
        <span class="text-xs w-20">autostop:</span>
        <input
          v-model.number="local.triggers.silence_minutes.autostop_rounds"
          type="number"
          min="1"
          :max="local.triggers.silence_minutes.autostop_max_default"
          class="border rounded px-1 py-0.5 w-20 text-xs"
          :disabled="readonly || !local.triggers.silence_minutes.enabled"
          @change="emitUpdate"
        />
      </label>
      <label class="flex items-center gap-2">
        <span class="text-xs w-20">max cap:</span>
        <input
          v-model.number="local.triggers.silence_minutes.autostop_max_default"
          type="number"
          min="1"
          max="100"
          class="border rounded px-1 py-0.5 w-20 text-xs"
          :disabled="readonly || !local.triggers.silence_minutes.enabled"
          @change="emitUpdate"
        />
      </label>
    </fieldset>

    <!-- call_only -->
    <fieldset class="border rounded p-2">
      <legend class="text-xs font-medium px-1">call_only</legend>
      <label class="flex items-center gap-2">
        <input
          v-model="local.triggers.call_only.enabled"
          type="checkbox"
          :disabled="readonly"
          @change="emitUpdate"
        />
        <span class="text-xs">Enabled (ignores other triggers)</span>
      </label>
    </fieldset>

    <!-- Global flags -->
    <div class="space-y-1">
      <label class="flex items-center gap-2">
        <input
          v-model="local.allow_self_open"
          type="checkbox"
          :disabled="readonly"
          @change="emitUpdate"
        />
        <span class="text-xs">allow_self_open</span>
      </label>
      <label class="flex items-center gap-2">
        <span class="text-xs w-32">refresh_every_hours:</span>
        <input
          v-model.number="local.refresh_every_hours"
          type="number"
          min="1"
          max="720"
          class="border rounded px-1 py-0.5 w-20 text-xs"
          :disabled="readonly"
          @change="emitUpdate"
        />
      </label>
    </div>
  </div>
</template>
