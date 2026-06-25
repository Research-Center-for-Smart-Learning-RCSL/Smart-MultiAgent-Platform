<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { BellSlashIcon } from '@heroicons/vue/24/outline'
import { SButton, SEmptyState, SPageHeader, SQueryError } from '@shared/ui'
import { useNotificationsList } from '../composables/useNotificationsList'
import NotificationCard from '../components/NotificationCard.vue'

const { t } = useI18n()
const { query, items, isEmpty, hasUnread, marking, markOne, markAll } = useNotificationsList()
</script>

<template>
  <section class="notifications px-4 py-4 sm:p-6">
    <SPageHeader :title="t('notifications.title')">
      <template #actions>
        <SButton
          variant="secondary"
          size="sm"
          :disabled="!hasUnread || marking"
          :loading="marking"
          @click="markAll"
        >
          {{ t('notifications.markAll') }}
        </SButton>
      </template>
    </SPageHeader>

    <SQueryError
      v-if="query.isError.value"
      :message="t('notifications.loadError')"
      :retry-label="t('notifications.retry')"
      @retry="query.refetch()"
    />

    <p
      v-else-if="query.isLoading.value"
      class="notifications__loading"
      aria-live="polite"
    >
      {{ t('notifications.loading') }}
    </p>

    <ul
      v-else-if="items.length"
      class="notifications__list"
      role="list"
    >
      <NotificationCard
        v-for="n in items"
        :key="n.id"
        :notification="n"
        @mark-read="markOne"
      />
    </ul>

    <SEmptyState
      v-else-if="isEmpty"
      role="status"
      :icon="BellSlashIcon"
      :title="t('notifications.empty')"
      :text="t('notifications.emptyDescription')"
    />

    <div
      v-if="!query.isError.value && query.hasNextPage.value"
      class="notifications__more"
    >
      <SButton
        variant="secondary"
        :disabled="query.isFetchingNextPage.value"
        :loading="query.isFetchingNextPage.value"
        @click="query.fetchNextPage()"
      >
        {{ t('notifications.loadMore') }}
      </SButton>
    </div>
  </section>
</template>

<style scoped>
.notifications__loading {
  color: var(--color-muted);
  font-size: 0.875rem;
}
.notifications__list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.notifications__more {
  display: flex;
  justify-content: center;
  margin-top: 0.75rem;
}
</style>
