<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { Toaster } from 'vue-sonner'
import { ImpersonationBanner, useImpersonationFlag } from '@slices/admin'
import { useBanKickGuard } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { SConfirmDialog, SIdleDialog, SNetworkBanner } from '@shared/ui'
import ErrorBoundary from './ErrorBoundary.vue'
import AuthLayout from './layouts/AuthLayout.vue'
import AppShell from './layouts/AppShell.vue'
import PublicLayout from './layouts/PublicLayout.vue'
import { toasterProps } from './toasterProps'

useBanKickGuard()

const route = useRoute()
const { t } = useI18n()
const session = useSessionStore()
// The banner is the y = 0 element while this is true, so it owns the status-bar
// strip and the top bar below it must stop reserving one. The switch is a
// single inherited property on .app-root (main.css, --topbar-inset-top), so no
// consumer of the topbar height learns that impersonation exists.
const { isImpersonating } = useImpersonationFlag()

const layoutComponent = computed(() => {
  const layout = route.meta.layout as string | undefined
  if (layout === 'public') return PublicLayout
  if (layout === 'auth') return AuthLayout
  if (layout === 'app') return AppShell
  // 'auto' follows the session (02-layout-shell.md:351). The catch-all cannot
  // express this through requiresAuth: that would send a mistyped URL to
  // /login instead of rendering the 404 the visitor asked for.
  if (layout === 'auto') return session.isAuthenticated ? AppShell : AuthLayout
  return route.meta.requiresAuth ? AppShell : AuthLayout
})
</script>

<template>
  <!-- The impersonation banner shares this flow column with the layout, so it
       reserves its own height instead of painting over the top bar. The
       teleported siblings stay outside: the wrapper would constrain them and
       none of them takes part in flow anyway. -->
  <div
    class="app-root"
    :class="{ 'app-root--impersonating': isImpersonating }"
  >
    <ImpersonationBanner />
    <!-- Zero-height anchor, so the network banner's below-topbar offset is
         measured from wherever the impersonation banner ENDS rather than from
         a banner height this file would have to know. The banner is
         position: fixed in its default (unauthenticated) mode and unaffected
         by a non-transformed ancestor; only the below-topbar mode positions
         against this. -->
    <div class="app-root__overlay-anchor">
      <SNetworkBanner :below-topbar="layoutComponent === AppShell" />
    </div>
    <component :is="layoutComponent">
      <!-- Wraps only the routed view. A fallback rendered inside a shell that
           is itself broken is not a fallback, so a throw in the shell chrome
           deliberately propagates to app.config.errorHandler instead. -->
      <ErrorBoundary>
        <!-- Keyed on path (not fullPath): param navigation remounts the view,
             query-only changes (filters, pagination) never do. The route
             transition classes live in shared/styles/main.css. -->
        <router-view v-slot="{ Component }">
          <Transition
            name="route"
            mode="out-in"
          >
            <component
              :is="Component"
              :key="$route.path"
            />
          </Transition>
        </router-view>
      </ErrorBoundary>
    </component>
  </div>
  <!-- Toast visuals are token-themed in shared/styles/main.css (third-party
       overrides section) so they follow both themes; stock rich-colors is off. -->
  <Toaster v-bind="toasterProps(t)" />
  <SConfirmDialog />
  <SIdleDialog />
</template>

<style scoped>
/* min-height, never height: AuthLayout and PublicLayout size themselves and
   must keep scrolling the document. AppShell is the one layout that claims the
   remaining space, which it does with `flex: 1 1 0px` in its own stylesheet -
   a length basis, not `flex: 1`. See the note there before changing either.

   dvh, not vh (02-layout-shell.md:111-116). On mobile browsers `vh` resolves
   against the LARGE viewport, so a 100vh root is taller than the visible area
   by the toolbar height and the shell's bottom grid row - notably the chatroom
   composer - is below the fold on first paint. It also silently breaks
   useVisualViewport: that composable measures the keyboard against
   window.innerHeight, so a consumer sized against the large viewport
   over-shoots the visible band by exactly the toolbar height. */
.app-root {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
}

/* height: 0 so it reserves nothing, position: relative so the network banner's
   below-topbar mode resolves its `top` against this point in the flow. No
   z-index: `relative` alone builds no stacking context, so the banner keeps
   competing on --z-banner against the rest of the app. */
.app-root__overlay-anchor {
  position: relative;
  height: 0;
}
</style>
