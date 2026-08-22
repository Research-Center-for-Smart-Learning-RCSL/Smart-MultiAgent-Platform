<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Bars3Icon, XMarkIcon } from '@heroicons/vue/24/outline'
import { LocaleToggle, ThemeToggle } from '@shared/ui'
import { useBreakpoint } from '@shared/composables/useBreakpoint'
import { NotificationBell } from '@slices/notifications'
import UserMenu from './UserMenu.vue'
import OrgProjectSwitcher from './OrgProjectSwitcher.vue'

defineProps<{
  sidebarOpen: boolean
}>()

const emit = defineEmits<{
  'toggle-sidebar': []
}>()

const { t } = useI18n()
const { isMobile } = useBreakpoint()
</script>

<template>
  <header class="topbar">
    <!-- Left zone -->
    <div class="topbar__left">
      <button
        class="topbar__sidebar-toggle"
        type="button"
        :aria-label="t('app.topbar.toggleSidebar')"
        @click="emit('toggle-sidebar')"
      >
        <XMarkIcon
          v-if="sidebarOpen"
          class="topbar__toggle-icon"
        />
        <Bars3Icon
          v-else
          class="topbar__toggle-icon"
        />
      </button>

      <RouterLink
        to="/"
        class="topbar__wordmark"
      >
        SMAP
      </RouterLink>
    </div>

    <!-- Center zone — the switcher lives in the sidebar on desktop; on mobile
         the sidebar is a hidden drawer, so keep it here for quick switching. -->
    <div class="topbar__center">
      <OrgProjectSwitcher
        v-if="isMobile"
        compact
      />
    </div>

    <!-- Right zone -->
    <div class="topbar__right">
      <NotificationBell />
      <UserMenu />
      <LocaleToggle v-if="!isMobile" />
      <ThemeToggle v-if="!isMobile" />
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  display: flex;
  align-items: center;
  /* Grows into the status-bar strip and pads itself back out of it, so the
     content box stays --topbar-height while the background covers the strip.
     Shares --topbar-height-total with AppShell's first grid track, so the bar
     and the row it occupies cannot disagree. Horizontal insets are not
     repeated here: the bar sits inside the shell's padding box. */
  height: var(--topbar-height-total);
  padding: env(safe-area-inset-top, 0px) var(--space-4) 0;
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
  z-index: var(--z-topbar);
}

.topbar__left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-shrink: 0;
}

.topbar__sidebar-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-md);
  height: var(--control-h-md);
  padding: 0;
  background: none;
  border: none;
  border-radius: var(--radius-md);
  color: var(--color-fg);
  cursor: pointer;
  transition:
    background var(--transition-fast),
    transform var(--transition-fast);
}

.topbar__sidebar-toggle:hover {
  background: var(--color-surface-hover);
}

/* The shell's own controls follow the same press language as SButton, so the
   chrome does not feel less responsive than the content inside it. */
.topbar__sidebar-toggle:active {
  background: var(--color-surface-active);
  transform: translateY(1px);
}

.topbar__toggle-icon {
  width: 24px;
  height: 24px;
}

.topbar__wordmark {
  font-size: var(--font-size-lg);
  font-weight: var(--weight-bold);
  color: var(--color-accent);
  text-decoration: none;
  line-height: var(--line-none);
  user-select: none;
}

.topbar__wordmark:focus-visible {
  border-radius: var(--radius-md);
}

.topbar__center {
  flex: 1;
  display: flex;
  justify-content: center;
  min-width: 0;
}

.topbar__right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
  flex-shrink: 0;
}
</style>
