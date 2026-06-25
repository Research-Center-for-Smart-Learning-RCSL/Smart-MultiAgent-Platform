<template>
  <aside class="agent-sidebar">
    <p class="agent-sidebar__header">
      {{ t('conversation.chatroom.boundAgents', { count: agents.length }) }}
    </p>
    <ul class="agent-sidebar__list">
      <li
        v-for="a in agents"
        :key="a.id"
        class="agent-item"
      >
        <SAvatar
          :name="a.name"
          size="sm"
          :class="{ 'agent-item__avatar--busy': a.status === 'thinking' || a.status === 'streaming' }"
        />
        <span class="agent-item__body">
          <span class="agent-item__name">{{ a.name }}</span>
          <span
            class="agent-item__status"
            :class="`agent-item__status--${a.status}`"
          >{{ t(`conversation.chatroom.agentStatus.${a.status}`) }}</span>
        </span>
      </li>
    </ul>
    <p
      v-if="!agents.length"
      class="agent-sidebar__empty"
    >
      {{ t('conversation.chatroom.noBoundAgents') }}
    </p>
  </aside>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SAvatar } from '@shared/ui'

export type AgentStatus = 'idle' | 'thinking' | 'streaming' | 'error'

defineProps<{
  agents: Array<{ id: string; name: string; status: AgentStatus }>
}>()

const { t } = useI18n()
</script>

<style scoped>
.agent-sidebar {
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  padding: 16px;
  overflow-y: auto;
  height: 100%;
}

.agent-sidebar__header {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-muted);
  margin-bottom: 12px;
}

.agent-sidebar__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.agent-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
}

.agent-item__avatar--busy {
  box-shadow: 0 0 0 2px var(--color-accent);
  border-radius: var(--radius-full);
}

.agent-item__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.agent-item__name {
  font-size: 14px;
  color: var(--color-fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-item__status {
  font-size: 12px;
  color: var(--color-muted);
}

.agent-item__status--thinking,
.agent-item__status--streaming {
  color: var(--color-accent);
}

.agent-item__status--error {
  color: var(--color-danger);
}

.agent-sidebar__empty {
  font-size: 13px;
  color: var(--color-muted);
}
</style>
