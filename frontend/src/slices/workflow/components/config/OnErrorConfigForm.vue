<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
import { SFormField, SInput, SSelect } from '@shared/ui'
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
    // OnErrorConfig is plain JSON data (strategy + optional scalars), so its
    // runtime shape is genuinely compatible with Record<string, unknown>.
    return (props.modelValue ?? defaults()) as unknown as Record<string, unknown>
  },
}
const { local } = useConfigModel(
  configModelProps,
  emit as unknown as (event: 'update:modelValue', value: Record<string, unknown>) => void,
)

const strategyOptions = computed(() =>
  STRATEGIES.map((s) => ({ value: s, label: t(`workflow.config.errorStrategy_${s}`) })),
)

// SSelect option values cannot be null, so the "none" fallback option uses ''
// as sentinel; the stored config value stays null as before.
const fallbackNodeOptions = computed(() => [
  { value: '', label: t('workflow.config.noneFallback') },
  ...props.allNodeIds.map((nodeId) => ({ value: nodeId, label: nodeId })),
])

function emitUpdate(): void {
  // local always carries a 'strategy' field (set by defaults()/onStrategyChange),
  // so its runtime shape genuinely satisfies OnErrorConfig.
  emit('update:modelValue', { ...local } as unknown as OnErrorConfig)
}

function onStrategyChange(value: string | number): void {
  const strategy = value as OnErrorStrategy
  local.strategy = strategy

  // Reset strategy-specific fields when switching
  if (strategy !== 'retry') {
    delete local.retry_max
    delete local.retry_backoff_ms
  } else {
    local.retry_max = local.retry_max ?? 3
    local.retry_backoff_ms = local.retry_backoff_ms ?? 1000
  }
  if (strategy !== 'fallback') {
    delete local.fallback_node_id
  } else {
    local.fallback_node_id = local.fallback_node_id ?? null
  }

  emitUpdate()
}

// Reads the raw string from the native input event (which bubbles through
// SInput's wrapper) to mirror the previous v-model.number semantics: keep the
// raw string when it is not parseable instead of coercing '' to 0.
function onRetryInput(field: 'retry_max' | 'retry_backoff_ms', event: Event): void {
  const raw = (event.target as HTMLInputElement).value
  const parsed = parseFloat(raw)
  local[field] = Number.isNaN(parsed) ? raw : parsed
  emitUpdate()
}

function onFallbackChange(value: string | number): void {
  local.fallback_node_id = value === '' ? null : value
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
        <SSelect
          id="on-error-strategy"
          :model-value="(local.strategy as string)"
          :options="strategyOptions"
          @update:model-value="onStrategyChange"
        />
      </SFormField>

      <!-- Retry fields -->
      <template v-if="local.strategy === 'retry'">
        <SFormField
          :label="t('workflow.config.retryMax')"
          name="on-error-retry-max"
        >
          <SInput
            id="on-error-retry-max"
            type="number"
            :model-value="(local.retry_max as number) ?? 3"
            min="0"
            max="10"
            @input="onRetryInput('retry_max', $event)"
          />
        </SFormField>

        <SFormField
          :label="t('workflow.config.retryBackoffMs')"
          name="on-error-retry-backoff"
        >
          <SInput
            id="on-error-retry-backoff"
            type="number"
            :model-value="(local.retry_backoff_ms as number) ?? 1000"
            min="0"
            max="60000"
            step="100"
            @input="onRetryInput('retry_backoff_ms', $event)"
          />
        </SFormField>
      </template>

      <!-- Fallback field -->
      <SFormField
        v-if="local.strategy === 'fallback'"
        :label="t('workflow.config.fallbackNodeId')"
        name="on-error-fallback-node"
      >
        <SSelect
          id="on-error-fallback-node"
          :model-value="(local.fallback_node_id as string | null) ?? ''"
          :options="fallbackNodeOptions"
          @update:model-value="onFallbackChange"
        />
      </SFormField>
    </div>
  </details>
</template>
