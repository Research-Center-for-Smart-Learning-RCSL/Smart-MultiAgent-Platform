<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { ElMessage } from 'element-plus'

import { notificationsApi, type Notification } from '../api'
import { notificationKeys } from '../queries'

const { t } = useI18n()
const qc = useQueryClient()

const PAGE_SIZE = 50

const query = useInfiniteQuery({
  queryKey: notificationKeys.list(),
  queryFn: async ({ pageParam }) =>
    (await notificationsApi.list(pageParam as string | undefined, PAGE_SIZE)).data,
  initialPageParam: undefined as string | undefined,
  // A full page implies there may be more — page back from the last item's id.
  getNextPageParam: (lastPage: Notification[]) =>
    lastPage.length === PAGE_SIZE ? lastPage[lastPage.length - 1]!.id : undefined,
})

const items = computed<Notification[]>(() => (query.data.value?.pages ?? []).flat())
const hasUnread = computed(() => items.value.some((n) => !n.read_at))

const markReadMutation = useMutation({
  mutationFn: (ids: string[]) => notificationsApi.markRead(ids),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: notificationKeys.list() })
    qc.invalidateQueries({ queryKey: notificationKeys.unreadCount() })
  },
  onError: () => ElMessage.error(t('notifications.markFailed')),
})

function markOne(n: Notification): void {
  if (!n.read_at) markReadMutation.mutate([n.id])
}

// The backend marks specific ids; "mark all" operates on the currently-loaded
// unread set (there is no bulk endpoint), so load more first to mark older ones.
function markAllLoaded(): void {
  const ids = items.value.filter((n) => !n.read_at).map((n) => n.id)
  if (ids.length) markReadMutation.mutate(ids)
}

function fmt(iso: string): string {
  return new Date(iso).toLocaleString()
}
</script>

<template>
  <section class="notifications p-6">
    <div class="notifications__header">
      <h1 class="text-xl font-semibold">
        {{ t('notifications.title') }}
      </h1>
      <button
        class="btn"
        type="button"
        :disabled="!hasUnread || markReadMutation.isPending.value"
        @click="markAllLoaded"
      >
        {{ t('notifications.markAll') }}
      </button>
    </div>

    <p v-if="query.isLoading.value">
      {{ t('notifications.loading') }}
    </p>
    <ul
      v-else-if="items.length"
      class="notifications__list"
    >
      <li
        v-for="n in items"
        :key="n.id"
        class="notifications__item"
        :class="{ 'notifications__item--unread': !n.read_at }"
      >
        <div class="notifications__main">
          <span class="notifications__title">{{ n.title }}</span>
          <span
            v-if="n.body"
            class="notifications__body"
          >{{ n.body }}</span>
          <span class="notifications__meta">{{ n.kind }} · {{ fmt(n.created_at) }}</span>
        </div>
        <button
          v-if="!n.read_at"
          class="btn"
          type="button"
          @click="markOne(n)"
        >
          {{ t('notifications.markRead') }}
        </button>
      </li>
    </ul>
    <p
      v-else
      class="text-gray-500"
    >
      {{ t('notifications.empty') }}
    </p>

    <div
      v-if="query.hasNextPage.value"
      class="notifications__more"
    >
      <button
        class="btn"
        type="button"
        :disabled="query.isFetchingNextPage.value"
        @click="query.fetchNextPage()"
      >
        {{ t('notifications.loadMore') }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.notifications__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-4);
}
.notifications__list {
  list-style: none;
  padding: 0;
}
.notifications__item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-2);
}
.notifications__item--unread {
  border-left: 3px solid var(--color-primary, #2563eb);
  background: var(--color-surface, #f9fafb);
}
.notifications__main {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.notifications__title {
  font-weight: 600;
}
.notifications__body {
  color: var(--color-text, #374151);
}
.notifications__meta {
  color: var(--color-muted);
  font-size: 0.8rem;
}
.notifications__more {
  margin-top: var(--space-3);
}
</style>
