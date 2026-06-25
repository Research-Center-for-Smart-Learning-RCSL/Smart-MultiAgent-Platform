<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SButton } from '@shared/ui'
import { useNow } from '@shared/composables'
import { formatDateTime } from '@shared/utils/datetime'
import type { Notification } from '../api'
import { kindConfig } from '../lib/kindConfig'
import { relativeTime, type RelativeTime } from '../lib/relativeTime'

const props = defineProps<{ notification: Notification }>()
const emit = defineEmits<{ markRead: [id: string] }>()

const { t } = useI18n()

const cfg = computed(() => kindConfig(props.notification.kind))
const isUnread = computed(() => !props.notification.read_at)
const action = computed(() => cfg.value.action?.(props.notification) ?? null)
const kindLabel = computed(() => t(cfg.value.labelKey))
const absolute = computed(() => formatDateTime(props.notification.created_at))

function relLabel(r: RelativeTime): string {
  switch (r.unit) {
    case 'justNow':
      return t('notifications.justNow')
    case 'minutes':
      return t('notifications.minutesAgo', { n: r.n })
    case 'hours':
      return t('notifications.hoursAgo', { n: r.n })
    case 'yesterday':
      return t('notifications.yesterday')
    case 'days':
      return t('notifications.daysAgo', { n: r.n })
    case 'date':
      return r.value
  }
}
// `now` ticks every 60s so relative labels advance instead of freezing at the
// value captured on first render.
const now = useNow()
const relText = computed(() =>
  relLabel(relativeTime(props.notification.created_at, new Date(now.value))),
)

// Screen readers get the unread/read status plus the full text in one label so
// the list reads coherently without relying on the visual border treatment.
const ariaLabel = computed(() => {
  const status = isUnread.value
    ? t('notifications.statusUnread')
    : t('notifications.statusRead')
  const parts = [props.notification.title, props.notification.body, relText.value].filter(
    (p): p is string => Boolean(p),
  )
  return `${status}: ${parts.join(' — ')}`
})
</script>

<template>
  <li
    class="ncard"
    :class="{ 'ncard--unread': isUnread }"
    :aria-label="ariaLabel"
  >
    <span
      class="ncard__icon"
      :style="{ background: cfg.tintBg, color: cfg.iconColor }"
    >
      <component
        :is="cfg.icon"
        class="ncard__icon-svg"
        aria-hidden="true"
      />
    </span>

    <div class="ncard__main">
      <span class="ncard__title">{{ notification.title }}</span>
      <span
        v-if="notification.body"
        class="ncard__body"
      >{{ notification.body }}</span>
      <SButton
        v-if="action"
        class="ncard__link"
        variant="link"
        size="sm"
        as="router-link"
        :to="action.to"
      >
        {{ t(action.labelKey) }}
      </SButton>
      <span class="ncard__meta">{{ kindLabel }} &middot; {{ absolute }}</span>
    </div>

    <div class="ncard__aside">
      <time
        class="ncard__time"
        :datetime="notification.created_at"
      >{{ relText }}</time>
      <SButton
        v-if="isUnread"
        class="ncard__mark"
        variant="ghost"
        size="sm"
        :aria-label="t('notifications.markRead')"
        @click="emit('markRead', notification.id)"
      >
        {{ t('notifications.markRead') }}
      </SButton>
    </div>
  </li>
</template>

<style scoped>
.ncard {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-left: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 0.5rem;
  background: var(--color-bg);
}

.ncard--unread {
  border-left: 3px solid var(--color-accent);
  background: var(--color-surface);
}

.ncard__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
}

.ncard__icon-svg {
  width: 20px;
  height: 20px;
}

.ncard__main {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  flex: 1;
  min-width: 0;
}

.ncard__title {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-fg);
}

.ncard__body {
  font-size: 0.875rem;
  color: var(--color-fg);
  overflow-wrap: anywhere;
}

/* Read notifications recede: muted title + body. */
.ncard:not(.ncard--unread) .ncard__title,
.ncard:not(.ncard--unread) .ncard__body {
  color: var(--color-muted);
}

.ncard__link {
  align-self: flex-start;
}

.ncard__meta {
  font-size: 0.75rem;
  color: var(--color-muted);
}

.ncard__aside {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.5rem;
  flex-shrink: 0;
}

.ncard__time {
  font-size: 0.75rem;
  color: var(--color-muted);
  white-space: nowrap;
}

/* Mobile: stack the action zone full-width below the content; keep the 44px
   touch target on the mark-read control. */
@media (max-width: 767px) {
  .ncard {
    flex-wrap: wrap;
  }
  .ncard__aside {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
    width: 100%;
  }
  .ncard__mark {
    flex: 1;
    min-height: 44px;
  }
}
</style>
