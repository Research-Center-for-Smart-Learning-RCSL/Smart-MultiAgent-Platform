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
  /* Fixed and outside every layout's padding box, so it carries its own inset.
     This is the unauthenticated case (auth and public layouts); the authenticated
     one sits below the top bar, which has already cleared the strip. */
  top: max(var(--space-3), env(safe-area-inset-top, 0px));
  left: 50%;
  transform: translateX(-50%);
  z-index: var(--z-banner, 350);
  width: min(640px, calc(100vw - 32px));
}

/* --topbar-height-total, not --topbar-height: the bar grows by the top safe-area
   inset, so on a device with a cutout its bottom edge is below 56px. This banner
   sits at --z-banner (350), above --z-topbar (200), so getting it wrong paints
   the banner over the top bar rather than below it.
   absolute, not fixed: App.vue's zero-height overlay anchor sits directly below
   the impersonation banner, so this offset stays correct while that banner is
   displacing the top bar without either file knowing the other's height. The
   authenticated shell does not scroll the document (.app-shell owns its scroll
   port), so absolute and fixed are indistinguishable in the one mode that uses
   this rule. */
.s-net-banner--below-topbar {
  position: absolute;
  top: calc(var(--topbar-height-total) + 12px);
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
