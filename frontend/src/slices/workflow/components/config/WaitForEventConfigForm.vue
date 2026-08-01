<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigModel, safeNumber } from '../../composables/useConfigModel'
import { SAlert, SCheckbox, SFormField, SInput, SSelect, STextarea } from '@shared/ui'

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

const eventTypeOptions = computed(() =>
  EVENT_TYPES.map((et) => ({ value: et, label: t('workflow.config.eventType_' + et) })),
)

const senderFilterOptions = computed(() =>
  SENDER_FILTERS.map((sf) => ({ value: sf, label: t('workflow.config.senderFilter_' + sf) })),
)

const chatroomOptions = computed(() => [
  { value: '', label: t('workflow.config.none') },
  ...props.chatrooms.map((room) => ({ value: room.id, label: room.name })),
])

const agentOptions = computed(() => [
  { value: '', label: t('workflow.config.none') },
  ...props.agents.map((agent) => ({ value: agent.id, label: agent.name })),
])

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
      <SSelect
        id="wait-event-type"
        :model-value="(local.event_type as string) ?? 'message_in_room'"
        :options="eventTypeOptions"
        @update:model-value="update('event_type', $event)"
      />
    </SFormField>

    <!-- Timeout — not applicable to timer waits (F-2): a timer has no external
         producer, so it resumes at `default` after delay_seconds instead of ever
         reaching the timeout port. -->
    <!-- Native @input (bubbles through SInput's wrapper) so safeNumber sees the
         raw string; SInput's update:modelValue coerces a cleared field to 0,
         which would bypass the min=1 fallback. -->
    <SFormField
      v-if="(local.event_type ?? 'message_in_room') !== 'timer'"
      :label="t('workflow.config.timeoutSeconds')"
      name="wait-timeout"
    >
      <SInput
        id="wait-timeout"
        :model-value="(local.timeout_seconds as number) ?? 300"
        type="number"
        min="1"
        max="86400"
        @input="update('timeout_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
      />
    </SFormField>

    <!-- message_in_room fields -->
    <template v-if="(local.event_type ?? 'message_in_room') === 'message_in_room'">
      <SFormField
        :label="t('workflow.config.chatroomId')"
        name="wait-chatroom"
      >
        <SSelect
          id="wait-chatroom"
          :model-value="(local.chatroom_id as string) ?? ''"
          :options="chatroomOptions"
          @update:model-value="update('chatroom_id', $event)"
        />
      </SFormField>

      <SFormField
        :label="t('workflow.config.senderFilter')"
        name="wait-sender-filter"
      >
        <SSelect
          id="wait-sender-filter"
          :model-value="(local.sender_filter as string) ?? 'any'"
          :options="senderFilterOptions"
          @update:model-value="update('sender_filter', $event)"
        />
      </SFormField>

      <SFormField
        :label="t('workflow.config.contentRegex')"
        name="wait-content-regex"
      >
        <SInput
          id="wait-content-regex"
          :model-value="(local.content_regex as string) ?? ''"
          type="text"
          @update:model-value="update('content_regex', $event)"
        />
      </SFormField>
    </template>

    <!-- a2a_message fields -->
    <template v-if="local.event_type === 'a2a_message'">
      <SFormField
        :label="t('workflow.config.agentId')"
        name="wait-agent"
      >
        <SSelect
          id="wait-agent"
          :model-value="(local.agent_id as string) ?? ''"
          :options="agentOptions"
          @update:model-value="update('agent_id', $event)"
        />
      </SFormField>

      <SFormField
        :label="t('workflow.config.types')"
        name="wait-a2a-types"
      >
        <div class="flex flex-col gap-1">
          <SCheckbox
            v-for="at in A2A_TYPES"
            :key="at"
            :model-value="getTypes().includes(at)"
            @update:model-value="toggleType(at)"
          >
            {{ t('workflow.config.eventType_' + at) }}
          </SCheckbox>
        </div>
      </SFormField>
    </template>

    <!-- timer fields -->
    <template v-if="local.event_type === 'timer'">
      <SAlert
        variant="info"
        :title="t('workflow.config.timerTimeoutNotApplicableTitle')"
      >
        {{ t('workflow.config.timerTimeoutNotApplicableBody') }}
      </SAlert>

      <SFormField
        :label="t('workflow.config.delaySeconds')"
        name="wait-delay"
      >
        <SInput
          id="wait-delay"
          :model-value="(local.delay_seconds as number) ?? 60"
          type="number"
          min="1"
          max="86400"
          @input="update('delay_seconds', safeNumber(($event.target as HTMLInputElement).value, 1))"
        />
      </SFormField>
    </template>

    <!-- variable_matches fields -->
    <template v-if="local.event_type === 'variable_matches'">
      <SFormField
        :label="t('workflow.config.expression')"
        name="wait-expression"
      >
        <STextarea
          id="wait-expression"
          :model-value="(local.expression as string) ?? ''"
          mono
          @update:model-value="update('expression', $event)"
        />
      </SFormField>
    </template>
  </div>
</template>
