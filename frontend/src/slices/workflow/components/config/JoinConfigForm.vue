<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import { SAlert, SFormField, SInput, SSelect } from '@shared/ui'

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

const joinModeOptions = computed(() =>
  JOIN_MODES.map((m) => ({ value: m, label: t('workflow.config.joinMode_' + m) })),
)
</script>

<template>
  <div class="space-y-4">
    <!-- The join fan-in never times out on its own (F-36); the timeout port and
         edges into it stay valid for stored definitions, but this field is gone
         because nothing reads it. -->
    <SAlert
      variant="warning"
      :title="t('workflow.config.joinTimeoutNotImplementedTitle')"
    >
      {{ t('workflow.config.joinTimeoutNotImplementedBody') }}
    </SAlert>

    <!-- Mode -->
    <SFormField
      :label="t('workflow.config.mode')"
      name="join-mode"
    >
      <SSelect
        id="join-mode"
        :model-value="(local.mode as string) ?? 'all'"
        :options="joinModeOptions"
        @update:model-value="update('mode', $event)"
      />
    </SFormField>

    <!-- Count (only when mode === 'count') -->
    <!-- Native @input (bubbles through SInput's wrapper) so safeNumber sees the
         raw string; SInput's update:modelValue coerces a cleared field to 0,
         which would bypass the min=1 fallback. -->
    <SFormField
      v-if="local.mode === 'count'"
      :label="t('workflow.config.count')"
      name="join-count"
    >
      <SInput
        id="join-count"
        :model-value="(local.count as number) ?? 1"
        type="number"
        min="1"
        max="50"
        @input="update('count', safeNumber(($event.target as HTMLInputElement).value, 1))"
      />
    </SFormField>
  </div>
</template>
