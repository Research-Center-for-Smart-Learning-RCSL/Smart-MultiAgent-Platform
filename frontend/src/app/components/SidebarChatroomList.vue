<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ChatBubbleLeftIcon } from '@heroicons/vue/24/outline'
import { useWorkspaceStore } from '@shared/stores/workspace'
import { useRecentChatrooms } from '@slices/conversation'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const workspace = useWorkspaceStore()

const { query: chatroomsQuery, rooms: chatrooms } = useRecentChatrooms(
  () => workspace.projectId,
  { limit: 10 },
)

function isActive(chatroomId: string): boolean {
  return route.path === `/chatrooms/${chatroomId}`
}

function truncate(text: string, max: number): string {
  return text.length > max ? text.slice(0, max) + '...' : text
}

function navigateTo(chatroomId: string): void {
  router.push(`/chatrooms/${chatroomId}`)
}
</script>

<template>
  <div class="chatroom-list">
    <div class="section-header">
      {{ t('app.sidebar.recentChatrooms') }}
    </div>

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
      <a
        v-for="chatroom in chatrooms"
        :key="chatroom.id"
        class="nav-item"
        :class="{ 'nav-item--active': isActive(chatroom.id) }"
        href="#"
        @click.prevent="navigateTo(chatroom.id)"
      >
        <ChatBubbleLeftIcon
          class="nav-icon"
        />
        <span class="nav-label">{{ truncate(chatroom.name, 20) }}</span>
      </a>
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
.section-header {
  text-transform: uppercase;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-sidebar-section-text);
  padding: 16px 16px 8px;
  letter-spacing: 0.05em;
}

.nav-item {
  display: flex;
  align-items: center;
  height: 40px;
  padding: 0 16px;
  gap: 12px;
  font-size: 14px;
  font-weight: 400;
  color: var(--color-sidebar-text);
  text-decoration: none;
  transition: background-color var(--transition-fast);
  cursor: pointer;
}

.nav-item:hover {
  background-color: var(--color-sidebar-hover);
}

.nav-item--active {
  background-color: var(--color-sidebar-active-bg);
  color: var(--color-sidebar-active-text);
  border-left: 3px solid var(--color-sidebar-active-text);
  padding-left: 13px;
}

.nav-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  color: inherit;
}

.nav-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  font-size: 12px;
  color: var(--color-muted);
  text-align: center;
  padding: 8px 16px;
}

.skeleton-container {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 16px;
}

.skeleton-line {
  height: 20px;
  border-radius: var(--radius-md);
  background-color: var(--color-border);
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
