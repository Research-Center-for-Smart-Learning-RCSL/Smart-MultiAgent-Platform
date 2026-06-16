<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import FormField from '@shared/ui/FormField.vue'

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
    <FormField
      :label="t('workflow.config.mode')"
      name="join-mode"
    >
      <select
        id="join-mode"
        :value="local.mode ?? 'all'"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @change="update('mode', ($event.target as HTMLSelectElement).value)"
      >
        <option
          v-for="m in JOIN_MODES"
          :key="m"
          :value="m"
        >
          {{ m }}
        </option>
      </select>
    </FormField>

    <!-- Count (only when mode === 'count') -->
    <FormField
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
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="update('count', safeNumber(($event.target as HTMLInputElement).value, 1))"
      >
    </FormField>

    <!-- Timeout -->
    <FormField
      :label="t('workflow.config.timeoutSeconds')"
      name="join-timeout"
    >
      <input
        id="join-timeout"
        :value="local.timeout_seconds ?? 600"
        type="number"
        min="1"
        max="86400"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="update('timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
      >
    </FormField>
  </div>
</template>
