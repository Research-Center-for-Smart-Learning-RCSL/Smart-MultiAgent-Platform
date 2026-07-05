<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import { SCheckbox, SFormField, SInput, SSelect, STextarea } from '@shared/ui'
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

const modeOptions = computed(() =>
  MODE_OPTIONS.map((m) => ({ value: m, label: t('workflow.config.approvalMode_' + m) })),
)

const leaderAgentOptions = computed(() => [
  { value: '', label: t('workflow.config.none'), disabled: true },
  ...props.agents.map((agent) => ({ value: agent.id, label: agent.name })),
])

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
      <SSelect
        id="approval-mode"
        :model-value="(local.mode as string) ?? 'single'"
        :options="modeOptions"
        @update:model-value="update('mode', $event)"
      />
    </SFormField>

    <!-- Leader Agent -->
    <SFormField
      :label="t('workflow.config.leaderAgentId')"
      name="leader-agent-id"
      required
    >
      <SSelect
        id="leader-agent-id"
        :model-value="(local.leader_agent_id as string) ?? ''"
        :options="leaderAgentOptions"
        @update:model-value="update('leader_agent_id', $event)"
      />
    </SFormField>

    <!-- Approvers (checkbox per agent) -->
    <SFormField
      :label="t('workflow.config.approvers')"
      name="approvers"
    >
      <div class="flex flex-col gap-1">
        <SCheckbox
          v-for="agent in agents"
          :key="agent.id"
          :model-value="isApprover(agent.id)"
          @update:model-value="toggleApprover(agent.id)"
        >
          {{ agent.name }}
        </SCheckbox>
      </div>
    </SFormField>

    <!-- Timeout Seconds -->
    <!-- Native @input (bubbles through SInput's wrapper) so safeNumber sees the
         raw string; SInput's update:modelValue coerces a cleared field to 0,
         which would bypass the min=1 fallback. -->
    <SFormField
      :label="t('workflow.config.timeoutSeconds')"
      name="timeout-seconds"
    >
      <SInput
        id="timeout-seconds"
        type="number"
        :model-value="(local.timeout_seconds as number) ?? 3600"
        min="1"
        max="86400"
        @input="update('timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
      />
    </SFormField>

    <!-- Question Template -->
    <SFormField
      :label="t('workflow.config.questionTemplate')"
      name="question-template"
      required
    >
      <STextarea
        id="question-template"
        :model-value="(local.question_template as string) ?? ''"
        mono
        @update:model-value="update('question_template', $event)"
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
