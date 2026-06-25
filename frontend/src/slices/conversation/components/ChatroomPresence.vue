<template>
  <aside class="presence">
    <p class="presence__header">
      {{ t('conversation.chatroom.onlineCount', { count: onlineUsers.length }) }}
    </p>
    <ul class="presence__list">
      <li
        v-for="u in onlineUsers"
        :key="u.id"
        class="presence-user"
      >
        <span class="presence-user__avatar">
          <SAvatar
            :name="u.id"
            size="sm"
          />
          <span class="presence-user__dot" />
        </span>
        <span class="presence-user__name">{{ u.id.slice(0, 8) }}</span>
        <span
          v-if="u.isYou"
          class="presence-user__you"
        >{{ t('conversation.chatroom.you') }}</span>
      </li>
    </ul>

    <SDivider />

    <p class="presence__header">
      {{ t('conversation.chatroom.agentStatusHeader') }}
    </p>
    <ul class="presence__list">
      <ChatroomAgentStatusItem
        v-for="a in agents"
        :key="a.id"
        :agent="a"
      />
    </ul>
  </aside>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SAvatar, SDivider } from '@shared/ui'
import ChatroomAgentStatusItem, {
  type AgentStatusEntry,
} from './ChatroomAgentStatusItem.vue'

defineProps<{
  onlineUsers: Array<{ id: string; isYou: boolean }>
  agents: AgentStatusEntry[]
}>()

const { t } = useI18n()
</script>

<style scoped>
.presence {
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  padding: 16px;
  overflow-y: auto;
  height: 100%;
}

.presence__header {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-muted);
  margin: 12px 0 8px;
}

.presence__header:first-child {
  margin-top: 0;
}

.presence__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.presence-user {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
}

.presence-user__avatar {
  position: relative;
  display: inline-flex;
}

.presence-user__dot {
  position: absolute;
  right: -1px;
  bottom: -1px;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-success);
  border: 1px solid var(--color-surface);
}

.presence-user__name {
  font-size: 14px;
  color: var(--color-fg);
}

.presence-user__you {
  font-size: 12px;
  color: var(--color-muted);
}
</style>
