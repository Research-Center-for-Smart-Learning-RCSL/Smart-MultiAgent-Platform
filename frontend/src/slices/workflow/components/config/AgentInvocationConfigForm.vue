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

// Defaults
if (local.stream_to_chatroom === undefined) {
  local.stream_to_chatroom = true
}
if (local.timeout_seconds === undefined) {
  local.timeout_seconds = 120
}
</script>

<template>
  <div class="space-y-3">
    <!-- Agent ID (required) -->
    <SFormField
      :label="t('workflow.config.agentId')"
      name="agent-id"
      required
    >
      <select
        id="agent-id"
        :value="local.agent_id ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @change="update('agent_id', ($event.target as HTMLSelectElement).value)"
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

    <!-- Input Template (required) -->
    <SFormField
      :label="t('workflow.config.inputTemplate')"
      name="input-template"
      required
    >
      <textarea
        id="input-template"
        :value="(local.input_template as string) ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg min-h-[60px] font-mono"
        @input="update('input_template', ($event.target as HTMLTextAreaElement).value)"
      />
    </SFormField>

    <!-- Output Variable -->
    <SFormField
      :label="t('workflow.config.outputVariable')"
      name="output-variable"
    >
      <input
        id="output-variable"
        type="text"
        :value="(local.output_variable as string) ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="update('output_variable', ($event.target as HTMLInputElement).value)"
      >
    </SFormField>

    <!-- Target Chatroom (optional, with Default option) -->
    <SFormField
      :label="t('workflow.config.targetChatroomId')"
      name="target-chatroom-id"
    >
      <select
        id="target-chatroom-id"
        :value="local.target_chatroom_id ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @change="update('target_chatroom_id', ($event.target as HTMLSelectElement).value || null)"
      >
        <option value="">
          {{ t('workflow.config.defaultChatroom') }}
        </option>
        <option
          v-for="room in chatrooms"
          :key="room.id"
          :value="room.id"
        >
          {{ room.name }}
        </option>
      </select>
    </SFormField>

    <!-- Stream to Chatroom -->
    <SFormField
      :label="t('workflow.config.streamToChatroom')"
      name="stream-to-chatroom"
    >
      <label class="flex items-center gap-2">
        <input
          type="checkbox"
          :checked="local.stream_to_chatroom !== false"
          @change="update('stream_to_chatroom', ($event.target as HTMLInputElement).checked)"
        >
        <span class="text-sm">{{ t('workflow.config.streamToChatroom') }}</span>
      </label>
    </SFormField>

    <!-- Timeout Seconds -->
    <SFormField
      :label="t('workflow.config.timeoutSeconds')"
      name="timeout-seconds"
    >
      <input
        id="timeout-seconds"
        type="number"
        :value="local.timeout_seconds ?? 120"
        min="1"
        max="600"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="update('timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
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
