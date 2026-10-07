<template>
  <SCard>
    <h2 class="guest-bans__heading">
      {{ t('conversation.settings.bannedGuests') }}
    </h2>
    <p class="guest-bans__desc mb-2">
      {{ t('conversation.settings.bannedGuestsHelp') }}
    </p>
    <SSkeleton
      v-if="pending"
      :lines="2"
    />
    <SAlert
      v-else-if="failed"
      variant="danger"
    >
      {{ t('conversation.settings.bansLoadFailed') }}
    </SAlert>
    <p
      v-else-if="!bans.length"
      class="guest-bans__desc"
      data-testid="no-banned-guests"
    >
      {{ t('conversation.settings.noBannedGuests') }}
    </p>
    <ul
      v-else
      class="guest-bans__list"
    >
      <li
        v-for="ban in bans"
        :key="ban.id"
        class="guest-bans__row"
        data-testid="banned-guest"
      >
        <span class="guest-bans__name">{{ ban.display_name }}</span>
        <span class="guest-bans__time">{{ t('conversation.settings.bannedAt', { time: formatDateTime(ban.created_at) }) }}</span>
        <SButton
          variant="ghost"
          size="sm"
          :loading="isUnbanning(ban.id)"
          @click="emit('unban', ban.id)"
        >
          {{ t('conversation.settings.unban') }}
        </SButton>
      </li>
    </ul>
  </SCard>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SAlert, SButton, SCard, SSkeleton } from '@shared/ui'
import type { GuestBanOut } from '@shared/api-client'
import { formatDateTime } from '../utils/format'

defineProps<{
  bans: GuestBanOut[]
  pending: boolean
  failed: boolean
  isUnbanning: (banId: string) => boolean
}>()

const emit = defineEmits<{
  unban: [banId: string]
}>()

const { t } = useI18n()
</script>

<style scoped>
/* Mirrors the settings view's section headings. */
.guest-bans__heading {
  font-size: var(--font-size-lg);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin-bottom: var(--space-4);
}

.guest-bans__desc {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  margin-top: var(--space-0-5);
}

.guest-bans__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.guest-bans__row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 36px;
}

.guest-bans__name {
  font-size: var(--font-size-sm);
  color: var(--color-fg);
}

.guest-bans__time {
  flex: 1;
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}
</style>
