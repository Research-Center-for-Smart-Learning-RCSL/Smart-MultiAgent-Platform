<script setup lang="ts">
// Collaboration log ([R34.03], [R34.06]): what the analyst agent told the
// teacher agent and how it answered, and every student agent reply the teacher
// agent reviewed. Hidden when empty, like the alerts list: most rooms have
// neither.
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { ChatBubbleLeftRightIcon, ShieldCheckIcon } from '@heroicons/vue/24/outline'
import { SCard, SQueryError, SRelativeTime } from '@shared/ui'
import { useDashboardCollaboration } from '../composables/useDashboardCollaboration'
import type { CollaborationEntry } from '../types'

const props = defineProps<{
  workspaceId: string
}>()

const { t } = useI18n()
const { entries, error, refetch } = useDashboardCollaboration(() => props.workspaceId)

function outcomeLabel(entry: CollaborationEntry): string {
  if (entry.kind === 'peer_review') return t(`dashboard.collaboration.peerReview.status.${entry.status}`)
  if (entry.decision) return t(`dashboard.collaboration.decision.${entry.decision}`)
  return t(`dashboard.collaboration.status.${entry.status}`)
}
</script>

<template>
  <section
    v-if="entries.length || error"
    aria-labelledby="dashboard-collaboration-heading"
  >
    <SCard class="p-4">
      <h2
        id="dashboard-collaboration-heading"
        class="mb-3 text-lg font-semibold text-[var(--color-fg)]"
      >
        {{ t('dashboard.collaboration.title') }}
      </h2>
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
          v-for="entry in entries"
          :key="entry.id"
          class="flex flex-wrap items-start gap-x-3 gap-y-1 py-2 text-sm"
          data-testid="collaboration-entry"
        >
          <ShieldCheckIcon
            v-if="entry.kind === 'peer_review'"
            class="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-muted)]"
            aria-hidden="true"
          />
          <ChatBubbleLeftRightIcon
            v-else
            class="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-muted)]"
            aria-hidden="true"
          />
          <div class="min-w-0 flex-1 space-y-1">
            <template v-if="entry.kind === 'peer_review'">
              <p class="text-xs font-medium text-[var(--color-muted)]">
                {{ t('dashboard.collaboration.peerReview.kind') }}
              </p>
              <p class="whitespace-pre-wrap text-[var(--color-fg)] [overflow-wrap:anywhere]">
                <span class="text-[var(--color-muted)]">{{ t('dashboard.collaboration.peerReview.draft') }}</span>
                {{ entry.draft }}
              </p>
              <p
                v-if="entry.revised_text"
                class="whitespace-pre-wrap text-[var(--color-fg)] [overflow-wrap:anywhere]"
                data-testid="collaboration-revised"
              >
                <span class="text-[var(--color-muted)]">{{ t('dashboard.collaboration.peerReview.revised') }}</span>
                {{ entry.revised_text }}
              </p>
              <p
                v-if="entry.expire_reason"
                class="text-xs text-[var(--color-muted)]"
                data-testid="collaboration-expired"
              >
                {{ t(`dashboard.collaboration.peerReview.expire.${entry.expire_reason}`) }}
              </p>
            </template>
            <template v-else>
              <p class="text-xs font-medium text-[var(--color-muted)]">
                {{ t(`dashboard.collaboration.kind.${entry.error_kind}`) }}
              </p>
              <p class="text-[var(--color-fg)] [overflow-wrap:anywhere]">
                {{ entry.error }}
              </p>
              <p class="text-[var(--color-fg)] [overflow-wrap:anywhere]">
                <span class="text-[var(--color-muted)]">{{ t('dashboard.collaboration.suggestion') }}</span>
                {{ entry.suggestion }}
              </p>
            </template>
            <p
              v-if="entry.reason"
              class="text-[var(--color-fg)] [overflow-wrap:anywhere]"
              data-testid="collaboration-reason"
            >
              <span class="text-[var(--color-muted)]">{{ t('dashboard.collaboration.reason') }}</span>
              {{ entry.reason }}
            </p>
            <p class="text-xs text-[var(--color-muted)]">
              <RouterLink
                class="text-[var(--color-accent)]"
                :to="{ name: 'conversation.chatroom', params: { chatroomId: entry.room_id } }"
              >
                {{ entry.room_name }}
              </RouterLink>
              <span aria-hidden="true"> · </span>
              <span>{{
                t('dashboard.collaboration.route', {
                  from: entry.from_agent_name ?? t('dashboard.alerts.unknownAgent'),
                  to: entry.to_agent_name ?? t('dashboard.alerts.unknownAgent'),
                })
              }}</span>
              <span aria-hidden="true"> · </span>
              <SRelativeTime :value="entry.created_at" />
            </p>
          </div>
          <span
            class="shrink-0 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-muted)]"
          >
            {{ outcomeLabel(entry) }}
          </span>
        </li>
      </ul>
    </SCard>
  </section>
</template>
