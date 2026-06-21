<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import { SFormField } from '@shared/ui'

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

const EVENT_TYPES = ['message_in_room', 'a2a_message', 'timer', 'variable_matches'] as const
const SENDER_FILTERS = ['any', 'user', 'agent', 'guest'] as const
const A2A_TYPES = ['call', 'reply', 'notify', 'instruct'] as const

function getTypes(): string[] {
  if (Array.isArray(local.types)) {
    return local.types as string[]
  }
  return []
}

function toggleType(type: string) {
  const current = getTypes()
  const idx = current.indexOf(type)
  const next = structuredClone(current)
  if (idx === -1) {
    next.push(type)
  } else {
    next.splice(idx, 1)
  }
  update('types', next)
}
</script>

<template>
  <div class="space-y-4">
    <!-- Event type -->
    <SFormField
      :label="t('workflow.config.eventType')"
      name="wait-event-type"
    >
      <select
        id="wait-event-type"
        :value="local.event_type ?? 'message_in_room'"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @change="update('event_type', ($event.target as HTMLSelectElement).value)"
      >
        <option
          v-for="et in EVENT_TYPES"
          :key="et"
          :value="et"
        >
          {{ et }}
        </option>
      </select>
    </SFormField>

    <!-- Timeout -->
    <SFormField
      :label="t('workflow.config.timeoutSeconds')"
      name="wait-timeout"
    >
      <input
        id="wait-timeout"
        :value="local.timeout_seconds ?? 300"
        type="number"
        min="1"
        max="86400"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="update('timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
      >
    </SFormField>

    <!-- message_in_room fields -->
    <template v-if="(local.event_type ?? 'message_in_room') === 'message_in_room'">
      <SFormField
        :label="t('workflow.config.chatroomId')"
        name="wait-chatroom"
      >
        <select
          id="wait-chatroom"
          :value="local.chatroom_id ?? ''"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          @change="update('chatroom_id', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">
            {{ t('workflow.config.none') }}
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

      <SFormField
        :label="t('workflow.config.senderFilter')"
        name="wait-sender-filter"
      >
        <select
          id="wait-sender-filter"
          :value="local.sender_filter ?? 'any'"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          @change="update('sender_filter', ($event.target as HTMLSelectElement).value)"
        >
          <option
            v-for="sf in SENDER_FILTERS"
            :key="sf"
            :value="sf"
          >
            {{ sf }}
          </option>
        </select>
      </SFormField>

      <SFormField
        :label="t('workflow.config.contentRegex')"
        name="wait-content-regex"
      >
        <input
          id="wait-content-regex"
          :value="local.content_regex ?? ''"
          type="text"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          @input="update('content_regex', ($event.target as HTMLInputElement).value)"
        >
      </SFormField>
    </template>

    <!-- a2a_message fields -->
    <template v-if="local.event_type === 'a2a_message'">
      <SFormField
        :label="t('workflow.config.agentId')"
        name="wait-agent"
      >
        <select
          id="wait-agent"
          :value="local.agent_id ?? ''"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          @change="update('agent_id', ($event.target as HTMLSelectElement).value)"
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

      <SFormField
        :label="t('workflow.config.types')"
        name="wait-a2a-types"
      >
        <div class="space-y-1">
          <label
            v-for="at in A2A_TYPES"
            :key="at"
            class="flex items-center gap-2"
          >
            <input
              type="checkbox"
              :checked="getTypes().includes(at)"
              @change="toggleType(at)"
            >
            <span class="text-sm">{{ at }}</span>
          </label>
        </div>
      </SFormField>
    </template>

    <!-- timer fields -->
    <template v-if="local.event_type === 'timer'">
      <SFormField
        :label="t('workflow.config.delaySeconds')"
        name="wait-delay"
      >
        <input
          id="wait-delay"
          :value="local.delay_seconds ?? 60"
          type="number"
          min="1"
          max="86400"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          @input="update('delay_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
        >
      </SFormField>
    </template>

    <!-- variable_matches fields -->
    <template v-if="local.event_type === 'variable_matches'">
      <SFormField
        :label="t('workflow.config.expression')"
        name="wait-expression"
      >
        <textarea
          id="wait-expression"
          :value="(local.expression as string) ?? ''"
          class="w-full text-sm border rounded px-2 py-1 bg-bg min-h-[60px] font-mono"
          @input="update('expression', ($event.target as HTMLTextAreaElement).value)"
        />
      </SFormField>
    </template>
  </div>
</template>
