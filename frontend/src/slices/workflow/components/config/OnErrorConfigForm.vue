<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
import SFormField from '@shared/ui/SFormField.vue'
import type { OnErrorConfig, OnErrorStrategy } from '../../types'

const { t } = useI18n()

const props = defineProps<{
  modelValue: OnErrorConfig | undefined
  allNodeIds: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: OnErrorConfig): void
}>()

const STRATEGIES: OnErrorStrategy[] = ['fail', 'continue', 'retry', 'fallback']

function defaults(): OnErrorConfig {
  return { strategy: 'fail' }
}

const configModelProps = {
  get modelValue() {
    return (props.modelValue ?? defaults()) as Record<string, unknown>
  },
}
const { local } = useConfigModel(
  configModelProps,
  emit as unknown as (event: 'update:modelValue', value: Record<string, unknown>) => void,
)

function emitUpdate(): void {
  emit('update:modelValue', { ...local } as OnErrorConfig)
}

function onStrategyChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value as OnErrorStrategy
  local.strategy = value

  // Reset strategy-specific fields when switching
  if (value !== 'retry') {
    delete local.retry_max
    delete local.retry_backoff_ms
  } else {
    local.retry_max = local.retry_max ?? 3
    local.retry_backoff_ms = local.retry_backoff_ms ?? 1000
  }
  if (value !== 'fallback') {
    delete local.fallback_node_id
  } else {
    local.fallback_node_id = local.fallback_node_id ?? null
  }

  emitUpdate()
}
</script>

<template>
  <details class="border rounded p-2">
    <summary class="cursor-pointer text-sm font-medium select-none">
      {{ t('workflow.config.errorHandling') }}
    </summary>

    <div class="mt-2 space-y-2">
      <SFormField
        :label="t('workflow.config.errorStrategy')"
        name="on-error-strategy"
      >
        <select
          id="on-error-strategy"
          :value="local.strategy"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          @change="onStrategyChange"
        >
          <option
            v-for="s in STRATEGIES"
            :key="s"
            :value="s"
          >
            {{ t(`workflow.config.errorStrategy_${s}`) }}
          </option>
        </select>
      </SFormField>

      <!-- Retry fields -->
      <template v-if="local.strategy === 'retry'">
        <SFormField
          :label="t('workflow.config.retryMax')"
          name="on-error-retry-max"
        >
          <input
            id="on-error-retry-max"
            v-model.number="local.retry_max"
            type="number"
            min="0"
            max="10"
            class="w-full text-sm border rounded px-2 py-1 bg-bg"
            @input="emitUpdate"
          >
        </SFormField>

        <SFormField
          :label="t('workflow.config.retryBackoffMs')"
          name="on-error-retry-backoff"
        >
          <input
            id="on-error-retry-backoff"
            v-model.number="local.retry_backoff_ms"
            type="number"
            min="0"
            max="60000"
            step="100"
            class="w-full text-sm border rounded px-2 py-1 bg-bg"
            @input="emitUpdate"
          >
        </SFormField>
      </template>

      <!-- Fallback field -->
      <SFormField
        v-if="local.strategy === 'fallback'"
        :label="t('workflow.config.fallbackNodeId')"
        name="on-error-fallback-node"
      >
        <select
          id="on-error-fallback-node"
          v-model="local.fallback_node_id"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          @change="emitUpdate"
        >
          <option :value="null">
            {{ t('workflow.config.noneFallback') }}
          </option>
          <option
            v-for="nodeId in allNodeIds"
            :key="nodeId"
            :value="nodeId"
          >
            {{ nodeId }}
          </option>
        </select>
      </SFormField>
    </div>
  </details>
</template>
