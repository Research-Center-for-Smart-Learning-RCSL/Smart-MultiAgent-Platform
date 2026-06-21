<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
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

// Ensure trigger_type has a default
if (!local.trigger_type) {
  local.trigger_type = 'manual'
}

const TRIGGER_TYPES = ['manual', 'cron', 'message_received', 'a2a_event', 'wakeup_signal'] as const

const ROLE_OPTIONS = ['Admin', 'OrgOwner', 'OrgMember', 'ProjectOwner', 'ProjectMember'] as const

const SENDER_FILTER_OPTIONS = ['any', 'user', 'agent', 'guest'] as const

const EVENT_TYPE_OPTIONS = ['call', 'reply', 'notify', 'instruct'] as const

// Technical placeholders (cron syntax / IANA timezone) — universal, not translated
const CRON_PLACEHOLDER = '0 9 * * MON-FRI'
const TIMEZONE_PLACEHOLDER = 'UTC'

function toggleArrayItem(field: string, item: string): void {
  const current = Array.isArray(local[field]) ? [...(local[field] as string[])] : []
  const idx = current.indexOf(item)
  if (idx === -1) {
    current.push(item)
  } else {
    current.splice(idx, 1)
  }
  update(field, current)
}

function isInArray(field: string, item: string): boolean {
  return Array.isArray(local[field]) && (local[field] as string[]).includes(item)
}
</script>

<template>
  <div class="space-y-3">
    <!-- Trigger Type -->
    <SFormField
      :label="t('workflow.config.triggerType')"
      name="trigger-type"
      required
    >
      <select
        id="trigger-type"
        :value="local.trigger_type ?? 'manual'"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @change="update('trigger_type', ($event.target as HTMLSelectElement).value)"
      >
        <option
          v-for="tt in TRIGGER_TYPES"
          :key="tt"
          :value="tt"
        >
          {{ tt }}
        </option>
      </select>
    </SFormField>

    <!-- Manual: allowed_roles checkboxes -->
    <div v-if="local.trigger_type === 'manual'">
      <SFormField
        :label="t('workflow.config.allowedRoles')"
        name="allowed-roles"
      >
        <div class="space-y-1">
          <label
            v-for="role in ROLE_OPTIONS"
            :key="role"
            class="flex items-center gap-2"
          >
            <input
              type="checkbox"
              :checked="isInArray('allowed_roles', role)"
              @change="toggleArrayItem('allowed_roles', role)"
            >
            <span class="text-sm">{{ role }}</span>
          </label>
        </div>
      </SFormField>
    </div>

    <!-- Cron: cron_expression + timezone -->
    <div
      v-if="local.trigger_type === 'cron'"
      class="space-y-3"
    >
      <SFormField
        :label="t('workflow.config.cronExpression')"
        name="cron-expression"
        required
      >
        <input
          id="cron-expression"
          type="text"
          :value="local.cron_expression ?? ''"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          :placeholder="CRON_PLACEHOLDER"
          @input="update('cron_expression', ($event.target as HTMLInputElement).value)"
        >
      </SFormField>

      <SFormField
        :label="t('workflow.config.timezone')"
        name="timezone"
      >
        <input
          id="timezone"
          type="text"
          :value="local.timezone ?? 'UTC'"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          :placeholder="TIMEZONE_PLACEHOLDER"
          @input="update('timezone', ($event.target as HTMLInputElement).value)"
        >
      </SFormField>
    </div>

    <!-- Message Received: chatroom_id, sender_filter, content_regex -->
    <div
      v-if="local.trigger_type === 'message_received'"
      class="space-y-3"
    >
      <SFormField
        :label="t('workflow.config.chatroomId')"
        name="chatroom-id"
      >
        <select
          id="chatroom-id"
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
        name="sender-filter"
      >
        <select
          id="sender-filter"
          :value="local.sender_filter ?? 'any'"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          @change="update('sender_filter', ($event.target as HTMLSelectElement).value)"
        >
          <option
            v-for="sf in SENDER_FILTER_OPTIONS"
            :key="sf"
            :value="sf"
          >
            {{ sf }}
          </option>
        </select>
      </SFormField>

      <SFormField
        :label="t('workflow.config.contentRegex')"
        name="content-regex"
      >
        <input
          id="content-regex"
          type="text"
          :value="local.content_regex ?? ''"
          class="w-full text-sm border rounded px-2 py-1 bg-bg"
          placeholder=".*"
          @input="update('content_regex', ($event.target as HTMLInputElement).value)"
        >
      </SFormField>
    </div>

    <!-- A2A Event: agent_id, event_types checkboxes -->
    <div
      v-if="local.trigger_type === 'a2a_event'"
      class="space-y-3"
    >
      <SFormField
        :label="t('workflow.config.agentId')"
        name="a2a-agent-id"
      >
        <select
          id="a2a-agent-id"
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
        :label="t('workflow.config.eventTypes')"
        name="event-types"
      >
        <div class="space-y-1">
          <label
            v-for="et in EVENT_TYPE_OPTIONS"
            :key="et"
            class="flex items-center gap-2"
          >
            <input
              type="checkbox"
              :checked="isInArray('event_types', et)"
              @change="toggleArrayItem('event_types', et)"
            >
            <span class="text-sm">{{ et }}</span>
          </label>
        </div>
      </SFormField>
    </div>

    <!-- Wakeup Signal: agent_id -->
    <div v-if="local.trigger_type === 'wakeup_signal'">
      <SFormField
        :label="t('workflow.config.agentId')"
        name="wakeup-agent-id"
      >
        <select
          id="wakeup-agent-id"
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
    </div>
  </div>
</template>
