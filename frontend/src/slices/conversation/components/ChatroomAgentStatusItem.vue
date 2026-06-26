<template>
  <li
    class="agent-status-item"
    :title="errorTooltip"
  >
    <SAvatar
      :name="agent.name"
      size="sm"
      :class="{ 'agent-status-item__avatar--busy': busy }"
    />
    <span class="agent-status-item__body">
      <span class="agent-status-item__name">{{ agent.name }}</span>
      <span
        class="agent-status-item__status"
        :class="`agent-status-item__status--${agent.status}`"
      >{{ t(`conversation.chatroom.agentStatus.${agent.status}`) }}</span>
    </span>
  </li>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SAvatar } from '@shared/ui'
import { AGENT_ERROR_MESSAGE_KEYS, AGENT_ERROR_FALLBACK_KEY } from '../constants/agentErrors'

export type AgentStatus = 'idle' | 'thinking' | 'streaming' | 'error'

export interface AgentStatusEntry {
  id: string
  name: string
  status: AgentStatus
  // Backend error kind from agent.finished{error}, set while status is 'error'.
  errorReason?: string
}

const props = defineProps<{
  agent: AgentStatusEntry
}>()

const { t } = useI18n()
const busy = computed(
  () => props.agent.status === 'thinking' || props.agent.status === 'streaming',
)
const errorTooltip = computed(() =>
  props.agent.status === 'error' && props.agent.errorReason
    ? t(AGENT_ERROR_MESSAGE_KEYS[props.agent.errorReason] ?? AGENT_ERROR_FALLBACK_KEY)
    : undefined,
)
</script>

<style scoped>
.agent-status-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
}

.agent-status-item__avatar--busy {
  box-shadow: 0 0 0 2px var(--color-accent);
  border-radius: var(--radius-full);
}

.agent-status-item__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.agent-status-item__name {
  font-size: 14px;
  color: var(--color-fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-status-item__status {
  font-size: 12px;
  color: var(--color-muted);
}

.agent-status-item__status--thinking,
.agent-status-item__status--streaming {
  color: var(--color-accent);
}

.agent-status-item__status--error {
  color: var(--color-danger);
}
</style>
