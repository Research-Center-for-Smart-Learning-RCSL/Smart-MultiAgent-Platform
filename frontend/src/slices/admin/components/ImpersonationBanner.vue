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
  /* In flow, not fixed: the banner is a row of App.vue's flex column, so it
     reserves its own height and the 56px top bar starts below it. Fixed
     positioning removed it from flow, leaving nothing able to account for it,
     and .app-shell's overflow: hidden meant no scroll could clear the top bar.
     Sticky keeps it visible on the two document-scrolling layouts. */
  position: sticky;
  top: 0;
  /* Same layer as the connection banner: above chrome, below modals and
     toasts. The literal 9999 it used to carry outranked the toast layer, and
     sonner's 24px top offset puts the first top-right toast inside this bar's
     ~36px, so an impersonating admin saw toasts clipped by it. */
  z-index: var(--z-banner, 350);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  /* Three edges, because this row is full-bleed and meets three. In portrait
     the cutout is the top inset; rotate a notched device and safe-area-inset-top
     goes to 0 while the sensor housing moves to the left/right insets, which a
     top-only fix would walk straight into.
     Top is additive and the sides are max(), and that difference is real: the
     side --space-4 is a gutter whose only job is to keep clear of the edge, so
     the larger of the two is right, while the top --space-2 is interior padding
     around text that has to survive under the strip - max() would swallow it on
     every notched device. Growing the banner pushes the shell down by the
     inset; that is what the browser did before viewport-fit=cover, not a
     regression. */
  padding: calc(var(--space-2) + env(safe-area-inset-top, 0px))
    max(var(--space-4), env(safe-area-inset-right, 0px)) var(--space-2)
    max(var(--space-4), env(safe-area-inset-left, 0px));
  background: var(--color-warning);
  color: var(--color-warning-on);
  font-weight: var(--weight-semibold);
  font-size: var(--font-size-sm);
}

.impersonation-banner__warning {
  font-style: italic;
  opacity: 0.8;
}
</style>
