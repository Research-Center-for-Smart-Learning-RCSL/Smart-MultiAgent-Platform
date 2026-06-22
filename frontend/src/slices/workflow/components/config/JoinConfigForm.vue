<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import { SFormField } from '@shared/ui'

const { t } = useI18n()

const JOIN_MODES = ['all', 'any', 'count'] as const

const props = defineProps<{
  modelValue: Record<string, unknown>
  agents: Array<{ id: string; name: string }>
  chatrooms: Array<{ id: string; name: string }>
  allNodeIds: string[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
}>()

const { local, update } = useConfigModel(props, emit)
</script>

<template>
  <div class="space-y-4">
    <!-- Mode -->
    <SFormField
      :label="t('workflow.config.mode')"
      name="join-mode"
    >
      <select
        id="join-mode"
        :value="local.mode ?? 'all'"
        class="wf-input"
        @change="update('mode', ($event.target as HTMLSelectElement).value)"
      >
        <option
          v-for="m in JOIN_MODES"
          :key="m"
          :value="m"
        >
          {{ t('workflow.config.joinMode_' + m) }}
        </option>
      </select>
    </SFormField>

    <!-- Count (only when mode === 'count') -->
    <SFormField
      v-if="local.mode === 'count'"
      :label="t('workflow.config.count')"
      name="join-count"
    >
      <input
        id="join-count"
        :value="local.count ?? 1"
        type="number"
        min="1"
        max="50"
        class="wf-input"
        @input="update('count', safeNumber(($event.target as HTMLInputElement).value, 1))"
      >
    </SFormField>

    <!-- Timeout -->
    <SFormField
      :label="t('workflow.config.timeoutSeconds')"
      name="join-timeout"
    >
      <input
        id="join-timeout"
        :value="local.timeout_seconds ?? 600"
        type="number"
        min="1"
        max="86400"
        class="wf-input"
        @input="update('timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
      >
    </SFormField>
  </div>
</template>
