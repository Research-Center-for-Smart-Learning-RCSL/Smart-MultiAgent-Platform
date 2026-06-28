<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { ClockIcon } from '@heroicons/vue/24/outline'
import { useIdleLogout } from '@shared/composables'
import SModal from './SModal.vue'
import SButton from './SButton.vue'

const { t } = useI18n()
const { warningActive, remainingSeconds, stayActive, logoutNow } = useIdleLogout()
</script>

<template>
  <SModal
    :open="warningActive"
    :title="t('app.idle.title')"
    size="sm"
    role="alertdialog"
    :closable="false"
    :close-on-backdrop="false"
    persistent
    @close="stayActive"
  >
    <p class="s-idle__message">
      {{ t('app.idle.message') }}
    </p>
    <div
      class="s-idle__countdown"
      role="timer"
      aria-live="assertive"
    >
      <ClockIcon
        class="s-idle__icon"
        aria-hidden="true"
      />
      <span>{{ t('app.idle.countdown', { seconds: remainingSeconds }) }}</span>
    </div>

    <template #footer>
      <SButton
        variant="ghost"
        @click="logoutNow"
      >
        {{ t('app.idle.logoutNow') }}
      </SButton>
      <SButton
        variant="primary"
        @click="stayActive"
      >
        {{ t('app.idle.stay') }}
      </SButton>
    </template>
  </SModal>
</template>

<style scoped>
.s-idle__message {
  font-size: 0.875rem;
  color: var(--color-muted);
  margin: 0;
  line-height: 1.5;
}

.s-idle__countdown {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 1rem;
  padding: 0.75rem 1rem;
  border-radius: var(--radius-md);
  background: var(--color-warning-tint);
  color: var(--color-warning-on);
  font-size: 0.9375rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.s-idle__icon {
  width: 1.25rem;
  height: 1.25rem;
  flex-shrink: 0;
}
</style>
