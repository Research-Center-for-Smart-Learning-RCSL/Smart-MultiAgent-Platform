<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import FormField from '@shared/ui/FormField.vue'
import OnErrorConfigForm from './OnErrorConfigForm.vue'
import type { OnErrorConfig } from '../../types'

const { t } = useI18n()

const props = defineProps<{
  modelValue: Record<string, unknown>
  agents: Array<{ id: string; name: string }>
  chatrooms: Array<{ id: string; name: string }>
  allNodeIds: string[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
}>()

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T
}

const local = reactive<Record<string, unknown>>({ ...props.modelValue })

watch(() => props.modelValue, (v) => {
  Object.assign(local, clone(v))
}, { deep: true })

function update(field: string, value: unknown) {
  local[field] = value
  emit('update:modelValue', { ...local })
}
</script>

<template>
  <div class="space-y-4">
    <!-- Issuer Agent -->
    <FormField :label="t('workflow.config.issuerAgentId')" name="instruct-issuer-agent">
      <select
        id="instruct-issuer-agent"
        :value="local.issuer_agent_id ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @change="update('issuer_agent_id', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">
          {{ t('workflow.config.none') }}
        </option>
        <option
          v-for="agent in agents"
          :key="agent.id"
          :value="agent.id"
        >
          {{ agent.name }}
        </option>
      </select>
    </FormField>

    <!-- Target Agent -->
    <FormField :label="t('workflow.config.targetAgentId')" name="instruct-target-agent">
      <select
        id="instruct-target-agent"
        :value="local.target_agent_id ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @change="update('target_agent_id', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">
          {{ t('workflow.config.none') }}
        </option>
        <option
          v-for="agent in agents"
          :key="agent.id"
          :value="agent.id"
        >
          {{ agent.name }}
        </option>
      </select>
    </FormField>

    <!-- Instruction Template -->
    <FormField :label="t('workflow.config.instructionTemplate')" name="instruct-template">
      <textarea
        id="instruct-template"
        :value="(local.instruction_template as string) ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg min-h-[60px] font-mono"
        @input="update('instruction_template', ($event.target as HTMLTextAreaElement).value)"
      />
    </FormField>

    <!-- Wait for completion -->
    <FormField :label="t('workflow.config.waitForCompletion')" name="instruct-wait">
      <label class="flex items-center gap-2">
        <input
          id="instruct-wait"
          type="checkbox"
          :checked="local.wait_for_completion !== false"
          @change="update('wait_for_completion', ($event.target as HTMLInputElement).checked)"
        />
        <span class="text-sm">{{ t('workflow.config.waitForCompletion') }}</span>
      </label>
    </FormField>

    <!-- Completion Timeout (only when wait_for_completion) -->
    <FormField
      v-if="local.wait_for_completion !== false"
      :label="t('workflow.config.completionTimeoutSeconds')"
      name="instruct-timeout"
    >
      <input
        id="instruct-timeout"
        :value="local.completion_timeout_seconds ?? 120"
        type="number"
        min="1"
        max="600"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="update('completion_timeout_seconds', Number(($event.target as HTMLInputElement).value))"
      />
    </FormField>

    <!-- Output Variable -->
    <FormField :label="t('workflow.config.outputVariable')" name="instruct-output-var">
      <input
        id="instruct-output-var"
        :value="(local.output_variable as string) ?? ''"
        type="text"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="update('output_variable', ($event.target as HTMLInputElement).value)"
      />
    </FormField>

    <!-- On Error -->
    <OnErrorConfigForm
      :model-value="(local.on_error as OnErrorConfig | undefined)"
      :all-node-ids="allNodeIds"
      @update:model-value="update('on_error', $event)"
    />
  </div>
</template>
