<script setup lang="ts">
// Global connection-recovery banner (§12 Shared Patterns §4.4). Fixed to the
// top of the viewport, it appears only while the app is offline and offers an
// immediate "Retry Now" alongside the automatic backoff probe.
import { useI18n } from 'vue-i18n'
import { useNetworkStatus } from '@shared/composables'
import SAlert from './SAlert.vue'
import SButton from './SButton.vue'

const { t } = useI18n()
const { online, retryNow } = useNetworkStatus()
</script>

<template>
  <Transition name="s-net-banner">
    <div
      v-if="!online"
      class="s-net-banner"
    >
      <SAlert
        variant="warning"
        :title="t('app.network.title')"
        class="s-net-banner__alert"
      >
        {{ t('app.network.message') }}
        <template #actions>
          <SButton
            variant="secondary"
            size="sm"
            @click="retryNow"
          >
            {{ t('app.network.retry') }}
          </SButton>
        </template>
      </SAlert>
    </div>
  </Transition>
</template>

<style scoped>
.s-net-banner {
  position: fixed;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
  z-index: var(--z-toast, 1000);
  width: min(640px, calc(100vw - 32px));
  margin-top: 12px;
}

.s-net-banner__alert {
  box-shadow: var(--shadow-lg);
}

.s-net-banner-enter-active,
.s-net-banner-leave-active {
  transition:
    opacity var(--transition-normal) ease,
    transform var(--transition-normal) ease;
}

.s-net-banner-enter-from,
.s-net-banner-leave-to {
  opacity: 0;
  transform: translate(-50%, -12px);
}
</style>
