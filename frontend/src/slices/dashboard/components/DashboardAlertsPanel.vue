<script setup lang="ts">
// Analyst alerts on the dashboard ([R34.02]). Owns its query so the view model
// stays about room status; rendered only when there is something to show, since
// most rooms have no analyst and an empty card would be noise.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { ExclamationTriangleIcon } from '@heroicons/vue/24/outline'
import { SCard, SQueryError, SRelativeTime } from '@shared/ui'
import { useDashboardAlerts } from '../composables/useDashboardAlerts'
import type { DashboardAlert } from '../types'

const props = defineProps<{
  workspaceId: string
}>()

const { t } = useI18n()
const { alerts, error, refetch } = useDashboardAlerts(() => props.workspaceId)

const openCount = computed(() => alerts.value.filter((a) => !a.first_action_at).length)

function stateLabel(alert: DashboardAlert): string {
  if (alert.first_action_at) {
    return t(`dashboard.alerts.action.${alert.first_action_kind ?? 'room_message'}`)
  }
  return alert.first_viewed_at ? t('dashboard.alerts.seen') : t('dashboard.alerts.new')
}
</script>

<template>
  <section
    v-if="alerts.length || error"
    aria-labelledby="dashboard-alerts-heading"
  >
    <SCard class="p-4">
      <div class="mb-3 flex items-start justify-between gap-3">
        <h2
          id="dashboard-alerts-heading"
          class="text-lg font-semibold text-[var(--color-fg)]"
        >
          {{ t('dashboard.alerts.title') }}
        </h2>
        <span
          v-if="openCount"
          class="rounded-full bg-[var(--color-warning-tint)] px-2 py-1 text-xs font-medium text-[var(--color-warning-on)]"
        >
          {{ t('dashboard.alerts.openCount', { count: openCount }) }}
        </span>
      </div>
      <SQueryError
        v-if="error"
        :message="error.message"
        :retry-label="t('dashboard.retry')"
        @retry="refetch()"
      />
      <ul
        v-else
        class="divide-y divide-[var(--color-border-subtle)]"
      >
        <li
          v-for="alert in alerts"
          :key="alert.observation_id"
          class="flex flex-wrap items-start gap-x-3 gap-y-1 py-2 text-sm"
          data-testid="dashboard-alert"
        >
          <ExclamationTriangleIcon
            class="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-warning)]"
            aria-hidden="true"
          />
          <div class="min-w-0 flex-1">
            <p class="text-[var(--color-fg)] [overflow-wrap:anywhere]">
              {{ alert.alert_reason || t('dashboard.alerts.noReason') }}
            </p>
            <p class="mt-0.5 text-xs text-[var(--color-muted)]">
              <RouterLink
                class="text-[var(--color-accent)]"
                :to="{ name: 'conversation.chatroom', params: { chatroomId: alert.room_id } }"
              >
                {{ alert.room_name }}
              </RouterLink>
              <span aria-hidden="true"> · </span>
              <span>{{ alert.agent_name ?? t('dashboard.alerts.unknownAgent') }}</span>
              <template v-if="alert.created_at">
                <span aria-hidden="true"> · </span>
                <SRelativeTime :value="alert.created_at" />
              </template>
            </p>
          </div>
          <span
            class="shrink-0 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-muted)]"
          >
            {{ stateLabel(alert) }}
          </span>
        </li>
      </ul>
    </SCard>
  </section>
</template>
