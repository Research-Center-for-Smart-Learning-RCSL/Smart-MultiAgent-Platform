<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import { SFormField, SCharCount } from '@shared/ui'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
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

const { local, update } = useConfigModel(props, emit)
</script>

<template>
  <div class="space-y-4">
    <!-- Issuer Agent -->
    <SFormField
      :label="t('workflow.config.issuerAgentId')"
      name="instruct-issuer-agent"
    >
      <select
        id="instruct-issuer-agent"
        :value="local.issuer_agent_id ?? ''"
        class="wf-input"
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
    </SFormField>

    <!-- Target Agent -->
    <SFormField
      :label="t('workflow.config.targetAgentId')"
      name="instruct-target-agent"
    >
      <select
        id="instruct-target-agent"
        :value="local.target_agent_id ?? ''"
        class="wf-input"
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
    </SFormField>

    <!-- Instruction Template -->
    <SFormField
      :label="t('workflow.config.instructionTemplate')"
      name="instruct-template"
    >
      <textarea
        id="instruct-template"
        :value="(local.instruction_template as string) ?? ''"
        class="wf-input-code"
        :maxlength="INPUT_LIMITS.CONFIG_TEXT"
        @input="update('instruction_template', ($event.target as HTMLTextAreaElement).value)"
      />
      <SCharCount
        :current="((local.instruction_template as string) ?? '').length"
        :max="INPUT_LIMITS.CONFIG_TEXT"
        hide-until-near
      />
    </SFormField>

    <!-- Wait for completion -->
    <SFormField
      :label="t('workflow.config.waitForCompletion')"
      name="instruct-wait"
    >
      <label class="flex items-center gap-2">
        <input
          id="instruct-wait"
          type="checkbox"
          :checked="local.wait_for_completion !== false"
          @change="update('wait_for_completion', ($event.target as HTMLInputElement).checked)"
        >
        <span class="text-sm">{{ t('workflow.config.waitForCompletion') }}</span>
      </label>
    </SFormField>

    <!-- Completion Timeout (only when wait_for_completion) -->
    <SFormField
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
        class="wf-input"
        @input="update('completion_timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
      >
    </SFormField>

    <!-- Output Variable -->
    <SFormField
      :label="t('workflow.config.outputVariable')"
      name="instruct-output-var"
    >
      <input
        id="instruct-output-var"
        :value="(local.output_variable as string) ?? ''"
        type="text"
        class="wf-input"
        @input="update('output_variable', ($event.target as HTMLInputElement).value)"
      >
    </SFormField>

    <!-- On Error -->
    <OnErrorConfigForm
      :model-value="(local.on_error as OnErrorConfig | undefined)"
      :all-node-ids="allNodeIds"
      @update:model-value="update('on_error', $event)"
    />
  </div>
</template>
