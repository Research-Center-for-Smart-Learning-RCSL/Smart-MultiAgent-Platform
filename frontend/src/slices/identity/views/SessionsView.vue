<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ComputerDesktopIcon } from '@heroicons/vue/24/outline'
import {
  SPageHeader, SCard, SButton, SBadge,
  SAlert, SSkeleton, SEmptyState,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { authApi, type Session } from '../api/auth'

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()

const sessions = ref<Session[]>([])
const loading = ref(true)
const loadError = ref(false)
const revokingId = ref<string | null>(null)

const sortedSessions = computed(() => {
  return [...sessions.value].sort((a, b) => {
    return new Date(b.last_used_at).getTime() - new Date(a.last_used_at).getTime()
  })
})

function parseUserAgent(ua: string | null): string {
  if (!ua) return 'Unknown device'
  const browserMatch = ua.match(/(Chrome|Firefox|Safari|Edge|Opera|Brave)\/[\d.]+/)
  const osMatch = ua.match(/(Windows|macOS|Mac OS X|Linux|Android|iOS|iPhone|iPad)/)
  const browser = browserMatch ? browserMatch[1] : 'Unknown browser'
  const os = osMatch ? osMatch[1].replace('Mac OS X', 'macOS') : 'Unknown OS'
  return `${browser} on ${os}`
}

function timeAgo(dateStr: string): string {
  const now = Date.now()
  const then = new Date(dateStr).getTime()
  const diffMs = now - then
  const diffSec = Math.floor(diffMs / 1000)
  const diffMin = Math.floor(diffSec / 60)
  const diffHour = Math.floor(diffMin / 60)
  const diffDay = Math.floor(diffHour / 24)

  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin} min ago`
  if (diffHour < 24) return `${diffHour}h ago`
  return `${diffDay}d ago`
}

function formatDate(dateStr: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(dateStr))
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = false
  try {
    const { data } = await authApi.listSessions()
    sessions.value = data
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function revoke(s: Session): Promise<void> {
  const confirmed = await confirm({
    title: t('identity.sessions.revoke'),
    message: t('identity.sessions.revokeConfirm'),
    confirmLabel: t('identity.sessions.revoke'),
    variant: 'warning',
  })
  if (!confirmed) return

  revokingId.value = s.id
  try {
    await authApi.revokeSession(s.id)
    sessions.value = sessions.value.filter(x => x.id !== s.id)
    toast.success(t('identity.sessions.revokeSuccess'))
  } catch (e: unknown) {
    const status = (e as { response?: { status?: number } })?.response?.status
    if (status === 404) {
      sessions.value = sessions.value.filter(x => x.id !== s.id)
      toast.warning(t('identity.sessions.alreadyRevoked'))
    } else {
      toast.error(t('identity.sessions.revokeError'))
    }
  } finally {
    revokingId.value = null
  }
}

onMounted(load)
</script>

<template>
  <div>
    <SPageHeader :title="$t('identity.sessions.title')" />

    <SCard class="sessions-card">
      <div
        v-if="loading"
        :aria-busy="true"
      >
        <div
          v-for="i in 3"
          :key="i"
          class="skeleton-row"
        >
          <SSkeleton
            variant="rect"
            height="80px"
          />
        </div>
      </div>

      <SAlert
        v-else-if="loadError"
        variant="danger"
      >
        {{ $t('identity.sessions.loadError') }}
        <template #actions>
          <SButton
            variant="secondary"
            size="sm"
            @click="load"
          >
            {{ $t('identity.sessions.retry') }}
          </SButton>
        </template>
      </SAlert>

      <SEmptyState
        v-else-if="sortedSessions.length === 0"
        :title="$t('identity.sessions.empty')"
        :icon="ComputerDesktopIcon"
      />

      <ul
        v-else
        role="list"
        class="session-list"
      >
        <li
          v-for="(s, idx) in sortedSessions"
          :key="s.id"
          class="session-item"
          :class="{ 'session-item--first': idx === 0 }"
        >
          <div class="session-info">
            <div class="session-device">
              {{ parseUserAgent(s.user_agent) }}
            </div>
            <div class="session-meta">
              <span class="session-ip">{{ s.ip_inet }}</span>
              <time
                :datetime="s.last_used_at"
                class="session-time"
                :title="$t('identity.sessions.created', { date: formatDate(s.created_at) })"
              >
                {{ $t('identity.sessions.lastUsed', { time: timeAgo(s.last_used_at) }) }}
              </time>
            </div>
          </div>
          <div class="session-actions">
            <SBadge
              v-if="idx === 0"
              variant="success"
              size="sm"
              :aria-label="$t('identity.sessions.currentLabel')"
            >
              {{ $t('identity.sessions.currentBadge') }}
            </SBadge>
            <SButton
              v-if="idx !== 0"
              variant="danger"
              size="sm"
              :loading="revokingId === s.id"
              :disabled="revokingId === s.id"
              :aria-label="$t('identity.sessions.revokeLabel', { device: parseUserAgent(s.user_agent) })"
              @click="revoke(s)"
            >
              {{ $t('identity.sessions.revoke') }}
            </SButton>
          </div>
        </li>
      </ul>
    </SCard>
  </div>
</template>

<style scoped>
.sessions-card {
  max-width: 640px;
}

.skeleton-row {
  margin-bottom: 12px;
}

.skeleton-row:last-child {
  margin-bottom: 0;
}

.session-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 0;
  border-top: 1px solid var(--color-border);
}

.session-item--first {
  border-top: none;
  padding-top: 0;
}

.session-info {
  min-width: 0;
  flex: 1;
}

.session-device {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-fg);
}

.session-meta {
  display: flex;
  gap: 12px;
  margin-top: 4px;
}

.session-ip {
  font-size: 0.75rem;
  color: var(--color-muted);
}

.session-time {
  font-size: 0.75rem;
  color: var(--color-muted);
}

.session-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  margin-left: 16px;
}

@media (max-width: 768px) {
  .sessions-card {
    max-width: none;
  }

  .session-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .session-actions {
    margin-left: 0;
  }
}
</style>
