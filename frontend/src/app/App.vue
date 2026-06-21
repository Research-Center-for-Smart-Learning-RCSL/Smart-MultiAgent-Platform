<script setup lang="ts">
import { Toaster } from 'vue-sonner'
import { ImpersonationBanner } from '@slices/admin'
import { NotificationBell } from '@slices/notifications'
import { useBanKickGuard } from '@shared/composables'
import { SConfirmDialog, ThemeToggle } from '@shared/ui'
import ErrorBoundary from './ErrorBoundary.vue'

useBanKickGuard()
</script>

<template>
  <ImpersonationBanner />
  <div class="app-chrome">
    <ErrorBoundary>
      <ThemeToggle />
    </ErrorBoundary>
    <ErrorBoundary>
      <NotificationBell />
    </ErrorBoundary>
  </div>
  <!--
    ErrorBoundary contains a render-time throw in any view to a retry
    fallback, instead of letting Vue blank the entire app (FE-11).

    Key by path so a dynamic-segment view (e.g. /chatrooms/A → /chatrooms/B)
    is fully remounted instead of reused — otherwise `setup()` does not re-run
    and route-param constants captured there stay stale. Keyed on `path`, not
    `fullPath`, so pure query-string changes (filters/pagination) don't force
    a needless remount.
  -->
  <ErrorBoundary>
    <router-view :key="$route.path" />
  </ErrorBoundary>
  <Toaster
    position="top-right"
    :duration="4000"
    rich-colors
  />
  <SConfirmDialog />
</template>

<style scoped>
.app-chrome {
  position: fixed;
  top: 0.75rem;
  right: 0.75rem;
  z-index: 10000;
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
</style>
