<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import FormField from '@shared/ui/FormField.vue'
import type { WorkflowNode, NodeType } from '../types'

import TriggerConfigForm from './config/TriggerConfigForm.vue'
import AgentInvocationConfigForm from './config/AgentInvocationConfigForm.vue'
import ApprovalGateConfigForm from './config/ApprovalGateConfigForm.vue'
import ConditionConfigForm from './config/ConditionConfigForm.vue'
import InstructConfigForm from './config/InstructConfigForm.vue'
import SubagentSpawnConfigForm from './config/SubagentSpawnConfigForm.vue'
import WaitForEventConfigForm from './config/WaitForEventConfigForm.vue'
import ParallelConfigForm from './config/ParallelConfigForm.vue'
import JoinConfigForm from './config/JoinConfigForm.vue'
import SetVariableConfigForm from './config/SetVariableConfigForm.vue'
import EndConfigForm from './config/EndConfigForm.vue'

const { t } = useI18n()

const props = defineProps<{
  node: WorkflowNode
  agents: Array<{ id: string; name: string }>
  chatrooms: Array<{ id: string; name: string }>
  allNodeIds: string[]
}>()

defineEmits<{
  (e: 'update:config', config: Record<string, unknown>): void
  (e: 'update:label', label: string): void
  (e: 'delete'): void
}>()

const CONFIG_FORM_MAP: Record<NodeType, unknown> = {
  trigger: TriggerConfigForm,
  agent_invocation: AgentInvocationConfigForm,
  approval_gate: ApprovalGateConfigForm,
  condition: ConditionConfigForm,
  instruct: InstructConfigForm,
  subagent_spawn: SubagentSpawnConfigForm,
  wait_for_event: WaitForEventConfigForm,
  parallel: ParallelConfigForm,
  join: JoinConfigForm,
  set_variable: SetVariableConfigForm,
  end: EndConfigForm,
}

const localLabel = ref(props.node.label ?? '')

watch(
  () => props.node.label,
  (v) => {
    localLabel.value = v ?? ''
  },
)

const configComponent = computed(() => CONFIG_FORM_MAP[props.node.type] ?? null)
</script>

<template>
  <div class="space-y-3">
    <!-- Node ID (read-only) -->
    <div class="text-xs text-gray-500 dark:text-gray-400">
      ID: {{ node.id }}
    </div>

    <!-- Label (editable) -->
    <FormField :label="t('workflow.config.label')" name="node-label">
      <input
        id="node-label"
        v-model="localLabel"
        type="text"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="$emit('update:label', localLabel)"
      />
    </FormField>

    <!-- Type-specific config form (dynamic component) -->
    <component
      :is="configComponent"
      :model-value="node.config"
      :agents="agents"
      :chatrooms="chatrooms"
      :all-node-ids="allNodeIds"
      @update:model-value="$emit('update:config', $event)"
    />

    <!-- Delete button -->
    <button
      v-if="node.type !== 'trigger'"
      class="w-full mt-4 px-3 py-1.5 text-sm rounded bg-red-600 text-white hover:bg-red-700"
      @click="$emit('delete')"
    >
      {{ t('workflow.config.deleteNode') }}
    </button>
  </div>
</template>
