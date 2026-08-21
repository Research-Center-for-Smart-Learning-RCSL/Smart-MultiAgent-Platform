<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { SDrawer } from '@shared/ui'
import { useBreakpoint } from '@shared/composables/useBreakpoint'
import AppTopBar from '../components/AppTopBar.vue'
import AppSidebar from '../components/AppSidebar.vue'

const route = useRoute()
const { isDesktop } = useBreakpoint()

// null = follow the route's auto behavior; true/false = explicit user choice
// for the current route. An explicit choice must be able to override
// autoCollapsed in BOTH directions, otherwise the toggle is dead on immersive
// routes (e.g. chatrooms) where autoCollapsed is always true.
const manualOverride = ref<boolean | null>(null)

const isImmersiveRoute = computed(() => {
  const path = route.path
  if (/^\/chatrooms\/[^/]+$/.test(path)) return true
  if (/\/workflows\/[^/]+\/edit$/.test(path)) return true
  return false
})

const autoCollapsed = computed(() =>
  isImmersiveRoute.value || route.meta.sidebarCollapsed === true,
)

const sidebarCollapsed = computed(() => {
  if (!isDesktop.value) return true
  if (manualOverride.value !== null) return manualOverride.value
  return autoCollapsed.value
})

const sidebarDrawerOpen = ref(false)

function toggleSidebar(): void {
  if (isDesktop.value) {
    manualOverride.value = !sidebarCollapsed.value
  } else {
    sidebarDrawerOpen.value = !sidebarDrawerOpen.value
  }
}

watch(
  () => route.path,
  () => {
    sidebarDrawerOpen.value = false
    manualOverride.value = null
  },
)

const noPadding = computed(() =>
  route.meta.contentPadding === 'none' || isImmersiveRoute.value,
)

// `true | undefined` (never false) so the attribute is fully removed when the
// sidebar is open — jsdom lacks the inert property and would otherwise
// serialize `inert="false"`, which a boolean attribute reads as true. The cast
// only narrows the static type past exactOptionalPropertyTypes (same pattern
// as SSelect's id binding); the runtime value is unchanged.
const sidebarInert = computed(() => (sidebarCollapsed.value || undefined) as unknown as boolean)

// Topbar depth: once the content region is scrolled, the topbar picks up a
// shadow so it reads as floating above the page. rAF-coalesced like every
// other scroll/pointer handler in the shell.
const contentEl = ref<HTMLElement | null>(null)
const scrolled = ref(false)
let scrollRaf = 0

function onContentScroll(): void {
  if (scrollRaf) return
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = 0
    scrolled.value = (contentEl.value?.scrollTop ?? 0) > 0
  })
}

onBeforeUnmount(() => {
  if (scrollRaf) cancelAnimationFrame(scrollRaf)
})
</script>

<template>
  <div
    class="app-shell"
    :class="{ 'app-shell--sidebar-collapsed': sidebarCollapsed }"
  >
    <a
      class="skip-link"
      href="#main-content"
    >{{ $t('app.skipToContent') }}</a>

    <AppTopBar
      class="app-shell__topbar"
      :class="{ 'app-shell__topbar--scrolled': scrolled }"
      :sidebar-open="!sidebarCollapsed || sidebarDrawerOpen"
      @toggle-sidebar="toggleSidebar"
    />

    <!-- Stays mounted on desktop so the grid track can tween on collapse;
         `inert` + delayed visibility keep the hidden sidebar out of the tab
         order and accessibility tree. -->
    <aside
      v-if="isDesktop"
      class="app-shell__sidebar"
      :class="{ 'app-shell__sidebar--collapsed': sidebarCollapsed }"
      :inert="sidebarInert"
    >
      <AppSidebar />
    </aside>

    <SDrawer
      v-if="!isDesktop"
      :open="sidebarDrawerOpen"
      side="left"
      size="sm"
      @close="sidebarDrawerOpen = false"
    >
      <AppSidebar />
    </SDrawer>

    <main
      id="main-content"
      ref="contentEl"
      tabindex="-1"
      class="app-shell__content"
      :class="{ 'app-shell__content--no-pad': noPadding }"
      @scroll.passive="onContentScroll"
    >
      <slot />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  grid-template-rows: var(--topbar-height) 1fr;
  /* Claims the space App.vue's flex column has left after the impersonation
     banner, rather than assuming the whole viewport. min-height: 0 lets the
     grid shrink to it; without it the content row's overflow would push the
     shell taller than its share. */
  flex: 1;
  min-height: 0;
  overflow: hidden;
  /* Collapse/expand tweens the first grid track instead of snapping.
     --transition-slow already carries duration + easing. */
  transition: grid-template-columns var(--transition-slow);
}

.app-shell--sidebar-collapsed {
  grid-template-columns: 0 1fr;
}

.app-shell__topbar {
  grid-column: 1 / -1;
  grid-row: 1;
  transition: box-shadow var(--transition-normal);
}

.app-shell__topbar--scrolled {
  box-shadow: var(--elevation-2);
}

.app-shell__sidebar {
  grid-column: 1;
  grid-row: 2;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
  background: var(--color-sidebar-bg);
  border-right: 1px solid var(--color-border);
  /* Visibility flips only after the track tween finishes, so the sidebar
     stays visible while it slides shut but is fully hidden (and inert) once
     closed. */
  visibility: visible;
  transition: visibility 0s linear;
}

.app-shell__sidebar--collapsed {
  visibility: hidden;
  /* Matches the --transition-slow duration so hiding waits for the tween. */
  transition-delay: 300ms;
}

.app-shell__content {
  grid-column: 2;
  grid-row: 2;
  overflow-y: auto;
  background: var(--color-bg);
  padding: 24px;
}

.app-shell__content--no-pad {
  padding: 0;
}

/* The content region is a skip-link target (tabindex="-1"); it should not
   show a focus ring when focused programmatically. */
.app-shell__content:focus-visible {
  box-shadow: none;
  outline: none;
}

@media (max-width: 1023px) {
  .app-shell {
    grid-template-columns: 0 1fr;
  }

  .app-shell__content {
    padding: 16px;
  }

  .app-shell__content--no-pad {
    padding: 0;
  }
}

@media (max-width: 479px) {
  .app-shell__content {
    padding: 8px;
  }

  .app-shell__content--no-pad {
    padding: 0;
  }
}
</style>
