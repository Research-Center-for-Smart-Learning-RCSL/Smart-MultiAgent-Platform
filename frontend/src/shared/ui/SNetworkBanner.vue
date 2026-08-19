<script setup lang="ts">
// Global connection-recovery banner (§12 Shared Patterns §4.4). Fixed to the
// top of its host layout, it appears only while the app is offline and offers an
// immediate "Retry Now" alongside the automatic backoff probe.
import { useI18n } from 'vue-i18n'
import { useNetworkStatus } from '@shared/composables'
import SAlert from './SAlert.vue'
import SButton from './SButton.vue'

const { t } = useI18n()
const { online, retryNow } = useNetworkStatus()

defineProps<{ belowTopbar?: boolean }>()
</script>

<template>
  <Transition name="s-net-banner">
    <div
      v-if="!online"
      class="s-net-banner"
      :class="{ 's-net-banner--below-topbar': belowTopbar }"
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
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: var(--z-banner, 350);
  width: min(640px, calc(100vw - 32px));
}

.s-net-banner--below-topbar {
  top: calc(var(--topbar-height) + 12px);
}

.s-net-banner__alert {
  box-shadow: var(--shadow-lg);
}

.s-net-banner-enter-active,
.s-net-banner-leave-active {
  transition:
    opacity var(--transition-normal),
    transform var(--transition-normal);
}

.s-net-banner-enter-from,
.s-net-banner-leave-to {
  opacity: 0;
  transform: translate(-50%, -12px);
}
</style>
