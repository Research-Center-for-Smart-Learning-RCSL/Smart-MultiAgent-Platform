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

// Defaults
if (local.stream_to_chatroom === undefined) {
  local.stream_to_chatroom = true
}
if (local.timeout_seconds === undefined) {
  local.timeout_seconds = 300
}

const agentOptions = computed(() => [
  { value: '', label: t('workflow.config.none'), disabled: true },
  ...props.agents.map((agent) => ({ value: agent.id, label: agent.name })),
])

const chatroomOptions = computed(() => [
  { value: '', label: t('workflow.config.defaultChatroom') },
  ...props.chatrooms.map((room) => ({ value: room.id, label: room.name })),
])
</script>

<template>
  <div class="space-y-3">
    <!-- Agent ID (required) -->
    <SFormField
      :label="t('workflow.config.agentId')"
      name="agent-id"
      required
    >
      <SSelect
        id="agent-id"
        :model-value="(local.agent_id as string) ?? ''"
        :options="agentOptions"
        @update:model-value="update('agent_id', $event)"
      />
    </SFormField>

    <!-- Input Template (required) -->
    <SFormField
      :label="t('workflow.config.inputTemplate')"
      name="input-template"
      required
    >
      <STextarea
        id="input-template"
        :model-value="(local.input_template as string) ?? ''"
        mono
        @update:model-value="update('input_template', $event)"
      />
    </SFormField>

    <!-- Output Variable -->
    <SFormField
      :label="t('workflow.config.outputVariable')"
      name="output-variable"
    >
      <SInput
        id="output-variable"
        type="text"
        :model-value="(local.output_variable as string) ?? ''"
        @update:model-value="update('output_variable', $event)"
      />
    </SFormField>

    <!-- Target Chatroom (optional, with Default option) -->
    <SFormField
      :label="t('workflow.config.targetChatroomId')"
      name="target-chatroom-id"
    >
      <SSelect
        id="target-chatroom-id"
        :model-value="(local.target_chatroom_id as string | null) ?? ''"
        :options="chatroomOptions"
        @update:model-value="update('target_chatroom_id', $event || null)"
      />
    </SFormField>

    <!-- Stream to Chatroom -->
    <SFormField
      :label="t('workflow.config.streamToChatroom')"
      name="stream-to-chatroom"
    >
      <SCheckbox
        :model-value="local.stream_to_chatroom !== false"
        @update:model-value="update('stream_to_chatroom', $event)"
      >
        {{ t('workflow.config.streamToChatroom') }}
      </SCheckbox>
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
        :model-value="(local.timeout_seconds as number) ?? 300"
        min="1"
        max="600"
        @input="update('timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
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
