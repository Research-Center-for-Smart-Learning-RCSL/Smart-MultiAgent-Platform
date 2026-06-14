<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import { useSessionStore } from '@slices/identity'
import { notificationsApi } from '../api'
import { notificationKeys } from '../queries'
import { useNotificationsSocket } from '../composables/useNotificationsSocket'

const { t } = useI18n()
const session = useSessionStore()
const authed = computed(() => !!session.me)

// Live updates over /ws/user/{id}; the query below is the source of truth and
// the socket invalidates it. A modest refetchInterval is the fallback if the
// socket drops.
useNotificationsSocket()

const unreadQuery = useQuery({
  queryKey: notificationKeys.unreadCount(),
  queryFn: async () => (await notificationsApi.unreadCount()).data.count,
  enabled: authed,
  refetchInterval: 60_000,
})

const count = computed(() => unreadQuery.data.value ?? 0)
const badge = computed(() => (count.value > 99 ? '99+' : String(count.value)))
</script>

<template>
  <RouterLink
    v-if="authed"
    class="notif-bell"
    :to="{ name: 'notifications.list' }"
    :aria-label="t('notifications.bellLabel', { count })"
  >
    <svg
      class="notif-bell__icon"
      viewBox="0 0 24 24"
      width="22"
      height="22"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
    <span
      v-if="count > 0"
      class="notif-bell__badge"
    >{{ badge }}</span>
  </RouterLink>
</template>

<style scoped>
.notif-bell {
  position: fixed;
  top: var(--space-3, 0.75rem);
  right: var(--space-3, 0.75rem);
  z-index: 50;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 9999px;
  color: var(--color-text, #1f2937);
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e5e7eb);
}
.notif-bell:hover {
  background: var(--color-surface-hover, #f3f4f6);
}
.notif-bell__badge {
  position: absolute;
  top: -2px;
  right: -2px;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 9999px;
  background: var(--color-danger, #dc2626);
  color: #fff;
  font-size: 0.7rem;
  line-height: 18px;
  text-align: center;
}
</style>
