<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
import { SCheckbox, SFormField, SInput, SSelect } from '@shared/ui'

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

const TRIGGER_TYPES = [
  'manual',
  'cron',
  'message_received',
  'a2a_event',
  'wakeup_signal',
  'activity_event',
] as const

const ROLE_OPTIONS = ['Admin', 'OrgOwner', 'OrgMember', 'ProjectOwner', 'ProjectMember'] as const

const SENDER_FILTER_OPTIONS = ['any', 'user', 'agent', 'guest'] as const

const EVENT_TYPE_OPTIONS = ['call', 'reply', 'notify', 'instruct'] as const

// Technical placeholders (cron syntax / IANA timezone) — universal, not translated
const CRON_PLACEHOLDER = '0 9 * * MON-FRI'
const TIMEZONE_PLACEHOLDER = 'UTC'

const triggerTypeOptions = computed(() =>
  TRIGGER_TYPES.map((tt) => ({ value: tt, label: t('workflow.config.triggerType_' + tt) })),
)

const senderFilterOptions = computed(() =>
  SENDER_FILTER_OPTIONS.map((sf) => ({ value: sf, label: t('workflow.config.senderFilter_' + sf) })),
)

const chatroomOptions = computed(() => [
  { value: '', label: t('workflow.config.none') },
  ...props.chatrooms.map((room) => ({ value: room.id, label: room.name })),
])

const agentOptions = computed(() => [
  { value: '', label: t('workflow.config.none') },
  ...props.agents.map((agent) => ({ value: agent.id, label: agent.name })),
])

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
      <SSelect
        id="trigger-type"
        :model-value="(local.trigger_type as string) ?? 'manual'"
        :options="triggerTypeOptions"
        @update:model-value="update('trigger_type', $event)"
      />
    </SFormField>

    <!-- Manual: allowed_roles checkboxes -->
    <div v-if="local.trigger_type === 'manual'">
      <SFormField
        :label="t('workflow.config.allowedRoles')"
        name="allowed-roles"
      >
        <div class="flex flex-col gap-1">
          <SCheckbox
            v-for="role in ROLE_OPTIONS"
            :key="role"
            :model-value="isInArray('allowed_roles', role)"
            @update:model-value="toggleArrayItem('allowed_roles', role)"
          >
            {{ t('workflow.config.role_' + role) }}
          </SCheckbox>
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
        <SInput
          id="cron-expression"
          type="text"
          :model-value="(local.cron_expression as string) ?? ''"
          :placeholder="CRON_PLACEHOLDER"
          @update:model-value="update('cron_expression', $event)"
        />
      </SFormField>

      <SFormField
        :label="t('workflow.config.timezone')"
        name="timezone"
      >
        <SInput
          id="timezone"
          type="text"
          :model-value="(local.timezone as string) ?? 'UTC'"
          :placeholder="TIMEZONE_PLACEHOLDER"
          @update:model-value="update('timezone', $event)"
        />
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
        <SSelect
          id="chatroom-id"
          :model-value="(local.chatroom_id as string) ?? ''"
          :options="chatroomOptions"
          @update:model-value="update('chatroom_id', $event)"
        />
      </SFormField>

      <SFormField
        :label="t('workflow.config.senderFilter')"
        name="sender-filter"
      >
        <SSelect
          id="sender-filter"
          :model-value="(local.sender_filter as string) ?? 'any'"
          :options="senderFilterOptions"
          @update:model-value="update('sender_filter', $event)"
        />
      </SFormField>

      <SFormField
        :label="t('workflow.config.contentRegex')"
        name="content-regex"
      >
        <SInput
          id="content-regex"
          type="text"
          :model-value="(local.content_regex as string) ?? ''"
          placeholder=".*"
          @update:model-value="update('content_regex', $event)"
        />
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
        <SSelect
          id="a2a-agent-id"
          :model-value="(local.agent_id as string) ?? ''"
          :options="agentOptions"
          @update:model-value="update('agent_id', $event)"
        />
      </SFormField>

      <SFormField
        :label="t('workflow.config.eventTypes')"
        name="event-types"
      >
        <div class="flex flex-col gap-1">
          <SCheckbox
            v-for="et in EVENT_TYPE_OPTIONS"
            :key="et"
            :model-value="isInArray('event_types', et)"
            @update:model-value="toggleArrayItem('event_types', et)"
          >
            {{ t('workflow.config.eventType_' + et) }}
          </SCheckbox>
        </div>
      </SFormField>
    </div>

    <!-- Wakeup Signal: agent_id -->
    <div v-if="local.trigger_type === 'wakeup_signal'">
      <SFormField
        :label="t('workflow.config.agentId')"
        name="wakeup-agent-id"
      >
        <SSelect
          id="wakeup-agent-id"
          :model-value="(local.agent_id as string) ?? ''"
          :options="agentOptions"
          @update:model-value="update('agent_id', $event)"
        />
      </SFormField>
    </div>

    <!-- Activity Event: chatroom_id, optional activity_type_key -->
    <div
      v-if="local.trigger_type === 'activity_event'"
      class="space-y-3"
    >
      <SFormField
        :label="t('workflow.config.chatroomId')"
        name="activity-chatroom-id"
        required
      >
        <SSelect
          id="activity-chatroom-id"
          :model-value="(local.chatroom_id as string) ?? ''"
          :options="chatroomOptions"
          @update:model-value="update('chatroom_id', $event)"
        />
      </SFormField>

      <SFormField
        :label="t('workflow.config.activityTypeKey')"
        name="activity-type-key"
      >
        <SInput
          id="activity-type-key"
          type="text"
          :model-value="(local.activity_type_key as string) ?? ''"
          :placeholder="t('workflow.config.activityTypeKeyPlaceholder')"
          @update:model-value="update('activity_type_key', $event)"
        />
      </SFormField>
    </div>
  </div>
</template>
