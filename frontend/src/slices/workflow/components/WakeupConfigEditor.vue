<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { WakeupConfig } from '../types'

const { t } = useI18n()

const props = defineProps<{
  modelValue: WakeupConfig
  readonly?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: WakeupConfig): void
}>()

// Plain-data deep clone. `structuredClone` throws DataCloneError on a Vue
// reactive proxy (which is what a v-model parent passes in), and WakeupConfig is
// pure JSON data, so a JSON round-trip is both safe and proxy-tolerant.
function clone(v: WakeupConfig): WakeupConfig {
  return JSON.parse(JSON.stringify(v)) as WakeupConfig
}

const local = reactive<WakeupConfig>(clone(props.modelValue))

watch(() => props.modelValue, (v) => {
  Object.assign(local, clone(v))
}, { deep: true })

function emitUpdate(): void {
  emit('update:modelValue', clone(local))
}

const isInert = computed(() =>
  !local.triggers.every_n_messages.enabled
  && !local.triggers.silence_minutes.enabled
  && !local.triggers.call_only.enabled
)
</script>

<template>
  <div class="wakeup-editor space-y-3 text-sm">
    <div
      v-if="isInert"
      class="text-xs text-yellow-600 bg-yellow-50 px-2 py-1 rounded"
    >
      {{ t('workflow.wakeup.inert') }}
    </div>

    <!-- every_n_messages -->
    <fieldset class="border rounded p-2">
      <legend class="text-xs font-medium px-1">
        {{ t('workflow.wakeup.everyNMessages') }}
      </legend>
      <label class="flex items-center gap-2 mb-1">
        <input
          v-model="local.triggers.every_n_messages.enabled"
          type="checkbox"
          :disabled="readonly"
          @change="emitUpdate"
        >
        <span class="text-xs">{{ t('workflow.wakeup.enabled') }}</span>
      </label>
      <label class="flex items-center gap-2">
        <span class="text-xs w-8">{{ t('workflow.wakeup.n') }}</span>
        <input
          v-model.number="local.triggers.every_n_messages.n"
          type="number"
          min="1"
          max="1000"
          class="border rounded px-1 py-0.5 w-20 text-xs"
          :disabled="readonly || !local.triggers.every_n_messages.enabled"
          @change="emitUpdate"
        >
      </label>
    </fieldset>

    <!-- silence_minutes -->
    <fieldset class="border rounded p-2">
      <legend class="text-xs font-medium px-1">
        {{ t('workflow.wakeup.silenceMinutes') }}
      </legend>
      <label class="flex items-center gap-2 mb-1">
        <input
          v-model="local.triggers.silence_minutes.enabled"
          type="checkbox"
          :disabled="readonly"
          @change="emitUpdate"
        >
        <span class="text-xs">{{ t('workflow.wakeup.enabled') }}</span>
      </label>
      <label class="flex items-center gap-2 mb-1">
        <span class="text-xs w-20">{{ t('workflow.wakeup.tMinutes') }}</span>
        <input
          v-model.number="local.triggers.silence_minutes.t_minutes"
          type="number"
          min="1"
          max="1440"
          class="border rounded px-1 py-0.5 w-20 text-xs"
          :disabled="readonly || !local.triggers.silence_minutes.enabled"
          @change="emitUpdate"
        >
      </label>
      <label class="flex items-center gap-2 mb-1">
        <span class="text-xs w-20">{{ t('workflow.wakeup.autostop') }}</span>
        <input
          v-model.number="local.triggers.silence_minutes.autostop_rounds"
          type="number"
          min="1"
          :max="local.triggers.silence_minutes.autostop_max_default"
          class="border rounded px-1 py-0.5 w-20 text-xs"
          :disabled="readonly || !local.triggers.silence_minutes.enabled"
          @change="emitUpdate"
        >
      </label>
      <label class="flex items-center gap-2">
        <span class="text-xs w-20">{{ t('workflow.wakeup.maxCap') }}</span>
        <input
          v-model.number="local.triggers.silence_minutes.autostop_max_default"
          type="number"
          min="1"
          max="100"
          class="border rounded px-1 py-0.5 w-20 text-xs"
          :disabled="readonly || !local.triggers.silence_minutes.enabled"
          @change="emitUpdate"
        >
      </label>
    </fieldset>

    <!-- call_only -->
    <fieldset class="border rounded p-2">
      <legend class="text-xs font-medium px-1">
        {{ t('workflow.wakeup.callOnly') }}
      </legend>
      <label class="flex items-center gap-2">
        <input
          v-model="local.triggers.call_only.enabled"
          type="checkbox"
          :disabled="readonly"
          @change="emitUpdate"
        >
        <span class="text-xs">{{ t('workflow.wakeup.callOnlyEnabled') }}</span>
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
        >
        <span class="text-xs">{{ t('workflow.wakeup.allowSelfOpen') }}</span>
      </label>
      <label class="flex items-center gap-2">
        <span class="text-xs w-32">{{ t('workflow.wakeup.refreshEveryHours') }}</span>
        <input
          v-model.number="local.refresh_every_hours"
          type="number"
          min="1"
          max="720"
          class="border rounded px-1 py-0.5 w-20 text-xs"
          :disabled="readonly"
          @change="emitUpdate"
        >
      </label>
    </div>
  </div>
</template>
