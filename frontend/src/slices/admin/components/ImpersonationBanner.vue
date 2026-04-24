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
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  background: var(--color-warning, #f59e0b);
  color: var(--color-warning-text, #000);
  font-weight: 600;
  font-size: 0.875rem;
}

.impersonation-banner__warning {
  font-style: italic;
  opacity: 0.8;
}
</style>
