<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { ChatBubbleLeftIcon } from '@heroicons/vue/24/outline'
import { useWorkspaceStore } from '@shared/stores/workspace'
import { useRecentChatrooms } from '@slices/conversation'
import SidebarNavItem from './SidebarNavItem.vue'
import SidebarSectionHeader from './SidebarSectionHeader.vue'

const { t } = useI18n()
const workspace = useWorkspaceStore()

const { query: chatroomsQuery, rooms: chatrooms } = useRecentChatrooms(
  () => workspace.projectId,
  { limit: 5 },
)
</script>

<template>
  <div class="chatroom-list">
    <SidebarSectionHeader :label="t('app.sidebar.recentChatrooms')" />

    <div
      v-if="chatroomsQuery.isLoading.value"
      class="skeleton-container"
    >
      <div class="skeleton-line" />
      <div class="skeleton-line" />
      <div class="skeleton-line" />
    </div>

    <div
      v-else-if="chatroomsQuery.isError.value"
      class="empty-state"
    >
      {{ t('app.sidebar.loadError') }}
    </div>

    <template v-else-if="chatrooms.length">
      <SidebarNavItem
        v-for="chatroom in chatrooms"
        :key="chatroom.id"
        :icon="ChatBubbleLeftIcon"
        :label="chatroom.name"
        :to="`/chatrooms/${chatroom.id}`"
      />
      <RouterLink
        :to="{ name: 'conversation.workspaces', params: { projectId: workspace.projectId! } }"
        class="show-more"
      >
        {{ t('app.sidebar.showMore') }}
      </RouterLink>
    </template>

    <div
      v-else
      class="empty-state"
    >
      {{ t('app.sidebar.noChatrooms') }}
    </div>
  </div>
</template>

<style scoped>
.empty-state {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  text-align: center;
  padding: var(--space-2) var(--space-4);
}

.show-more {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--color-accent);
  text-decoration: none;
  padding: var(--space-1) var(--space-4);
  transition: color var(--transition-fast);
}

.show-more:hover {
  color: var(--color-accent-hover);
}

.skeleton-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
}

.skeleton-line {
  height: 20px;
  border-radius: var(--radius-md);
  background-color: var(--color-border-subtle);
  animation: pulse 1.5s ease-in-out infinite;
}

.skeleton-line:nth-child(2) {
  width: 80%;
}

.skeleton-line:nth-child(3) {
  width: 60%;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}
</style>
