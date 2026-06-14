<script setup lang="ts">
import { ImpersonationBanner } from '@slices/admin'
import { NotificationBell } from '@slices/notifications'
import { useBanKickGuard } from '@shared/composables'
import ErrorBoundary from './ErrorBoundary.vue'

useBanKickGuard()
</script>

<template>
  <ImpersonationBanner />
  <!-- The bell is always-present chrome above the routed view; isolate it in
       its own boundary so a render/setup throw in it (query, socket, i18n)
       cannot escape past App and blank the whole app (FE-11). -->
  <ErrorBoundary>
    <NotificationBell />
  </ErrorBoundary>
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
</template>
