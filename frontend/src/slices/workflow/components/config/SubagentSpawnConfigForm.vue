<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import { SAlert, SCheckbox, SFormField, SInput, SSelect, STextarea } from '@shared/ui'
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
    <!-- The engine cannot execute this node; it exits its failure port immediately.
         The fields below stay editable so a saved workflow round-trips unchanged. -->
    <SAlert
      variant="warning"
      :title="t('workflow.config.subagentUnavailableTitle')"
    >
      {{ t('workflow.config.subagentUnavailableBody') }}
    </SAlert>

    <!-- Parent Agent -->
    <SFormField
      :label="t('workflow.config.parentAgentId')"
      name="subagent-parent-agent"
    >
      <SSelect
        id="subagent-parent-agent"
        :model-value="(local.parent_agent_id as string) ?? ''"
        :options="agentOptions"
        @update:model-value="update('parent_agent_id', $event)"
      />
    </SFormField>

    <!-- Task Template -->
    <SFormField
      :label="t('workflow.config.taskTemplate')"
      name="subagent-task-template"
    >
      <STextarea
        id="subagent-task-template"
        :model-value="(local.task_template as string) ?? ''"
        mono
        @update:model-value="update('task_template', $event)"
      />
    </SFormField>

    <!-- Max Alive Simultaneously -->
    <!-- Native @input (bubbles through SInput's wrapper) so safeNumber sees the
         raw string; SInput's update:modelValue coerces a cleared field to 0,
         which would bypass the min=1 fallback. -->
    <SFormField
      :label="t('workflow.config.maxAliveSimultaneously')"
      name="subagent-max-alive"
    >
      <SInput
        id="subagent-max-alive"
        :model-value="(local.max_alive_simultaneously as number) ?? 3"
        type="number"
        min="1"
        max="20"
        @input="update('max_alive_simultaneously', safeNumber(($event.target as HTMLInputElement).value, 1))"
      />
    </SFormField>

    <!-- Wait for All -->
    <SFormField
      :label="t('workflow.config.waitForAll')"
      name="subagent-wait-all"
    >
      <SCheckbox
        id="subagent-wait-all"
        :model-value="local.wait_for_all !== false"
        @update:model-value="update('wait_for_all', $event)"
      >
        {{ t('workflow.config.waitForAll') }}
      </SCheckbox>
    </SFormField>

    <!-- Timeout Seconds -->
    <SFormField
      :label="t('workflow.config.timeoutSeconds')"
      name="subagent-timeout"
    >
      <SInput
        id="subagent-timeout"
        :model-value="(local.timeout_seconds as number) ?? 180"
        type="number"
        min="1"
        max="600"
        @input="update('timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
      />
    </SFormField>

    <!-- Output Variable -->
    <SFormField
      :label="t('workflow.config.outputVariable')"
      name="subagent-output-var"
    >
      <SInput
        id="subagent-output-var"
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
