<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
import FormField from '@shared/ui/FormField.vue'

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

const returnVariablesDisplay = computed(() => {
  const raw = local.return_variables
  if (Array.isArray(raw)) {
    return raw.join(', ')
  }
  return ''
})

function onReturnVariablesInput(event: Event) {
  const text = (event.target as HTMLInputElement).value
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
    <FormField
      :label="t('workflow.config.status')"
      name="end-status"
    >
      <select
        id="end-status"
        :value="local.status ?? 'success'"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @change="update('status', ($event.target as HTMLSelectElement).value)"
      >
        <option
          v-for="s in STATUSES"
          :key="s"
          :value="s"
        >
          {{ s }}
        </option>
      </select>
    </FormField>

    <!-- Return variables -->
    <FormField
      :label="t('workflow.config.returnVariables')"
      :help="t('workflow.config.returnVariablesHelp')"
      name="end-return-vars"
    >
      <input
        id="end-return-vars"
        :value="returnVariablesDisplay"
        type="text"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="onReturnVariablesInput"
      >
    </FormField>

    <!-- Failure reason (only when status === 'failure') -->
    <FormField
      v-if="(local.status ?? 'success') === 'failure'"
      :label="t('workflow.config.failureReason')"
      name="end-failure-reason"
    >
      <textarea
        id="end-failure-reason"
        :value="(local.failure_reason as string) ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg min-h-[60px] font-mono"
        @input="update('failure_reason', ($event.target as HTMLTextAreaElement).value)"
      />
    </FormField>
  </div>
</template>
