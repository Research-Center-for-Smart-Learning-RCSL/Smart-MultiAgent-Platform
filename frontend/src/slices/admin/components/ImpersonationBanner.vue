<template>
  <div
    v-if="isImpersonating"
    class="impersonation-banner"
  >
    <span>{{ $t('admin.impersonation.banner', { adminId: impersonatedBy }) }}</span>
    <span class="impersonation-banner__warning">{{ $t('admin.impersonation.readOnly') }}</span>
  </div>
</template>

<script setup lang="ts">
import { useImpersonation } from '../composables/useImpersonation'

const { isImpersonating, impersonatedBy } = useImpersonation()
</script>

<style scoped>
.impersonation-banner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  /* Same layer as the connection banner: above chrome, below modals and
     toasts. The literal 9999 it used to carry outranked the toast layer, and
     sonner's 24px top offset puts the first top-right toast inside this bar's
     ~36px, so an impersonating admin saw toasts clipped by it. */
  z-index: var(--z-banner, 350);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  background: var(--color-warning);
  color: var(--color-warning-on);
  font-weight: 600;
  font-size: 0.875rem;
}

.impersonation-banner__warning {
  font-style: italic;
  opacity: 0.8;
}
</style>
