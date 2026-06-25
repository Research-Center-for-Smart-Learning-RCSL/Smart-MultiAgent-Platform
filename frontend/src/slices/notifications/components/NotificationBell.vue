<script setup lang="ts">
import { computed, onScopeDispose, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import { BellIcon } from '@heroicons/vue/24/outline'
import { useSessionStore } from '@shared/stores/session'
import { notificationsApi } from '../api'
import { notificationKeys } from '../queries'
import { useNotificationsSocket } from '../composables/useNotificationsSocket'

const { t } = useI18n()
const session = useSessionStore()
const authed = computed(() => !!session.me)

// A11y: announce arriving notifications to screen readers, driven by the actual
// `notification.created` WS event (not a change in the unread count, which would
// miss arrivals that coincide with a mark-read). The text clears shortly after
// so repeated arrivals re-trigger the live region.
const announce = ref('')
let clearTimer: ReturnType<typeof setTimeout> | undefined
function announceNew(): void {
  announce.value = t('notifications.newNotification')
  if (clearTimer) clearTimeout(clearTimer)
  clearTimer = setTimeout(() => {
    announce.value = ''
  }, 1000)
}
onScopeDispose(() => {
  if (clearTimer) clearTimeout(clearTimer)
})

// Live updates over /ws/user/{id}; the query below is the source of truth and
// the socket invalidates it. refetchOnWindowFocus (default) covers tab returns,
// so the interval is only a slow fallback for a silently-dropped socket — long,
// not a tight 60 s poll firing app-wide forever.
useNotificationsSocket(announceNew)

const unreadQuery = useQuery({
  queryKey: notificationKeys.unreadCount(),
  queryFn: async () => (await notificationsApi.unreadCount()).count,
  enabled: authed,
  refetchInterval: 300_000,
})

// Tolerant by design: on a failed/absent fetch the count falls back to 0, which
// hides the badge rather than surfacing an error on a passive header control.
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
    <BellIcon
      class="notif-bell__icon"
      aria-hidden="true"
    />
    <span
      v-if="count > 0"
      class="notif-bell__badge"
    >{{ badge }}</span>
  </RouterLink>
  <span
    v-if="authed"
    class="visually-hidden"
    aria-live="polite"
    aria-atomic="true"
  >{{ announce }}</span>
</template>

<style scoped>
.notif-bell {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-full);
  color: var(--color-fg);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}
.notif-bell__icon {
  width: 22px;
  height: 22px;
}
.notif-bell:hover {
  background: var(--color-border);
}
.notif-bell__badge {
  position: absolute;
  top: -2px;
  right: -2px;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: var(--radius-full);
  background: var(--color-danger);
  color: #fff;
  font-size: 0.75rem;
  line-height: 18px;
  text-align: center;
}
</style>
