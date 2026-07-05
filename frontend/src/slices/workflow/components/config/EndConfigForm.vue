<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
import { SFormField, SInput, SSelect, STextarea } from '@shared/ui'

const { t } = useI18n()

const STATUSES = ['success', 'failure'] as const

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

const statusOptions = computed(() =>
  STATUSES.map((s) => ({ value: s, label: t('workflow.config.endStatus_' + s) })),
)

const returnVariablesDisplay = computed(() => {
  const raw = local.return_variables
  if (Array.isArray(raw)) {
    return raw.join(', ')
  }
  return ''
})

function onReturnVariablesInput(value: string | number) {
  const text = String(value)
  const arr = text
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
  update('return_variables', arr)
}
</script>

<template>
  <div class="space-y-4">
    <!-- Status -->
    <SFormField
      :label="t('workflow.config.status')"
      name="end-status"
    >
      <SSelect
        id="end-status"
        :model-value="(local.status as string) ?? 'success'"
        :options="statusOptions"
        @update:model-value="update('status', $event)"
      />
    </SFormField>

    <!-- Return variables -->
    <SFormField
      :label="t('workflow.config.returnVariables')"
      :help="t('workflow.config.returnVariablesHelp')"
      name="end-return-vars"
    >
      <SInput
        id="end-return-vars"
        :model-value="returnVariablesDisplay"
        type="text"
        @update:model-value="onReturnVariablesInput"
      />
    </SFormField>

    <!-- Failure reason (only when status === 'failure') -->
    <SFormField
      v-if="(local.status ?? 'success') === 'failure'"
      :label="t('workflow.config.failureReason')"
      name="end-failure-reason"
    >
      <STextarea
        id="end-failure-reason"
        :model-value="(local.failure_reason as string) ?? ''"
        mono
        @update:model-value="update('failure_reason', $event)"
      />
    </SFormField>
  </div>
</template>
