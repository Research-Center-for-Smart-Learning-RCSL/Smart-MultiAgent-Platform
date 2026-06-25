<template>
  <aside class="agent-sidebar">
    <p class="agent-sidebar__header">
      {{ t('conversation.chatroom.boundAgents', { count: agents.length }) }}
    </p>
    <ul class="agent-sidebar__list">
      <ChatroomAgentStatusItem
        v-for="a in agents"
        :key="a.id"
        :agent="a"
      />
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
import ChatroomAgentStatusItem, {
  type AgentStatusEntry,
} from './ChatroomAgentStatusItem.vue'

defineProps<{
  agents: AgentStatusEntry[]
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

.agent-sidebar__empty {
  font-size: 13px;
  color: var(--color-muted);
}
</style>
