<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import { SCharCount, SCheckbox, SFormField, SInput, SSelect, STextarea } from '@shared/ui'
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

const agentOptions = computed(() => [
  { value: '', label: t('workflow.config.none') },
  ...props.agents.map((agent) => ({ value: agent.id, label: agent.name })),
])
</script>

<template>
  <div class="space-y-4">
    <!-- Issuer Agent -->
    <SFormField
      :label="t('workflow.config.issuerAgentId')"
      name="instruct-issuer-agent"
    >
      <SSelect
        id="instruct-issuer-agent"
        :model-value="(local.issuer_agent_id as string) ?? ''"
        :options="agentOptions"
        @update:model-value="update('issuer_agent_id', $event)"
      />
    </SFormField>

    <!-- Target Agent -->
    <SFormField
      :label="t('workflow.config.targetAgentId')"
      name="instruct-target-agent"
    >
      <SSelect
        id="instruct-target-agent"
        :model-value="(local.target_agent_id as string) ?? ''"
        :options="agentOptions"
        @update:model-value="update('target_agent_id', $event)"
      />
    </SFormField>

    <!-- Instruction Template -->
    <SFormField
      :label="t('workflow.config.instructionTemplate')"
      name="instruct-template"
    >
      <STextarea
        id="instruct-template"
        :model-value="(local.instruction_template as string) ?? ''"
        mono
        :maxlength="INPUT_LIMITS.CONFIG_TEXT"
        @update:model-value="update('instruction_template', $event)"
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
      <SCheckbox
        id="instruct-wait"
        :model-value="local.wait_for_completion !== false"
        @update:model-value="update('wait_for_completion', $event)"
      >
        {{ t('workflow.config.waitForCompletion') }}
      </SCheckbox>
    </SFormField>

    <!-- Completion Timeout (only when wait_for_completion) -->
    <!-- Native @input (bubbles through SInput's wrapper) so safeNumber sees the
         raw string; SInput's update:modelValue coerces a cleared field to 0,
         which would bypass the min=1 fallback. -->
    <SFormField
      v-if="local.wait_for_completion !== false"
      :label="t('workflow.config.completionTimeoutSeconds')"
      name="instruct-timeout"
    >
      <SInput
        id="instruct-timeout"
        :model-value="(local.completion_timeout_seconds as number) ?? 120"
        type="number"
        min="1"
        max="600"
        @input="update('completion_timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
      />
    </SFormField>

    <!-- Output Variable -->
    <SFormField
      :label="t('workflow.config.outputVariable')"
      name="instruct-output-var"
    >
      <SInput
        id="instruct-output-var"
        :model-value="(local.output_variable as string) ?? ''"
        type="text"
        @update:model-value="update('output_variable', $event)"
      />
    </SFormField>

    <!-- On Error -->
    <OnErrorConfigForm
      :model-value="(local.on_error as OnErrorConfig | undefined)"
      :all-node-ids="allNodeIds"
      @update:model-value="update('on_error', $event)"
    />
  </div>
</template>
