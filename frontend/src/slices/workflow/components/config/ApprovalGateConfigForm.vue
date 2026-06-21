<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import SFormField from '@shared/ui/SFormField.vue'
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

const MODE_OPTIONS = ['single', 'majority', 'consensus'] as const

function toggleApprover(agentId: string): void {
  const current = Array.isArray(local.approvers) ? [...(local.approvers as string[])] : []
  const idx = current.indexOf(agentId)
  if (idx === -1) {
    current.push(agentId)
  } else {
    current.splice(idx, 1)
  }
  update('approvers', current)
}

function isApprover(agentId: string): boolean {
  return Array.isArray(local.approvers) && (local.approvers as string[]).includes(agentId)
}
</script>

<template>
  <div class="space-y-3">
    <!-- Mode -->
    <SFormField
      :label="t('workflow.config.mode')"
      name="approval-mode"
      required
    >
      <select
        id="approval-mode"
        :value="local.mode ?? 'single'"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @change="update('mode', ($event.target as HTMLSelectElement).value)"
      >
        <option
          v-for="m in MODE_OPTIONS"
          :key="m"
          :value="m"
        >
          {{ m }}
        </option>
      </select>
    </SFormField>

    <!-- Leader Agent -->
    <SFormField
      :label="t('workflow.config.leaderAgentId')"
      name="leader-agent-id"
      required
    >
      <select
        id="leader-agent-id"
        :value="local.leader_agent_id ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @change="update('leader_agent_id', ($event.target as HTMLSelectElement).value)"
      >
        <option
          value=""
          disabled
        >
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

    <!-- Approvers (checkbox per agent) -->
    <SFormField
      :label="t('workflow.config.approvers')"
      name="approvers"
    >
      <div class="space-y-1">
        <label
          v-for="agent in agents"
          :key="agent.id"
          class="flex items-center gap-2"
        >
          <input
            type="checkbox"
            :checked="isApprover(agent.id)"
            @change="toggleApprover(agent.id)"
          >
          <span class="text-sm">{{ agent.name }}</span>
        </label>
      </div>
    </SFormField>

    <!-- Timeout Seconds -->
    <SFormField
      :label="t('workflow.config.timeoutSeconds')"
      name="timeout-seconds"
    >
      <input
        id="timeout-seconds"
        type="number"
        :value="local.timeout_seconds ?? 3600"
        min="1"
        max="86400"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="update('timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
      >
    </SFormField>

    <!-- Question Template -->
    <SFormField
      :label="t('workflow.config.questionTemplate')"
      name="question-template"
      required
    >
      <textarea
        id="question-template"
        :value="(local.question_template as string) ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg min-h-[60px] font-mono"
        @input="update('question_template', ($event.target as HTMLTextAreaElement).value)"
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
