<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import { SFormField } from '@shared/ui'
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
    <!-- Parent Agent -->
    <SFormField
      :label="t('workflow.config.parentAgentId')"
      name="subagent-parent-agent"
    >
      <select
        id="subagent-parent-agent"
        :value="local.parent_agent_id ?? ''"
        class="wf-input"
        @change="update('parent_agent_id', ($event.target as HTMLSelectElement).value)"
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

    <!-- Task Template -->
    <SFormField
      :label="t('workflow.config.taskTemplate')"
      name="subagent-task-template"
    >
      <textarea
        id="subagent-task-template"
        :value="(local.task_template as string) ?? ''"
        class="wf-input-code"
        @input="update('task_template', ($event.target as HTMLTextAreaElement).value)"
      />
    </SFormField>

    <!-- Max Alive Simultaneously -->
    <SFormField
      :label="t('workflow.config.maxAliveSimultaneously')"
      name="subagent-max-alive"
    >
      <input
        id="subagent-max-alive"
        :value="local.max_alive_simultaneously ?? 3"
        type="number"
        min="1"
        max="20"
        class="wf-input"
        @input="update('max_alive_simultaneously', safeNumber(($event.target as HTMLInputElement).value, 1))"
      >
    </SFormField>

    <!-- Wait for All -->
    <SFormField
      :label="t('workflow.config.waitForAll')"
      name="subagent-wait-all"
    >
      <label class="flex items-center gap-2">
        <input
          id="subagent-wait-all"
          type="checkbox"
          :checked="local.wait_for_all !== false"
          @change="update('wait_for_all', ($event.target as HTMLInputElement).checked)"
        >
        <span class="text-sm">{{ t('workflow.config.waitForAll') }}</span>
      </label>
    </SFormField>

    <!-- Timeout Seconds -->
    <SFormField
      :label="t('workflow.config.timeoutSeconds')"
      name="subagent-timeout"
    >
      <input
        id="subagent-timeout"
        :value="local.timeout_seconds ?? 180"
        type="number"
        min="1"
        max="600"
        class="wf-input"
        @input="update('timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
      >
    </SFormField>

    <!-- Output Variable -->
    <SFormField
      :label="t('workflow.config.outputVariable')"
      name="subagent-output-var"
    >
      <input
        id="subagent-output-var"
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
