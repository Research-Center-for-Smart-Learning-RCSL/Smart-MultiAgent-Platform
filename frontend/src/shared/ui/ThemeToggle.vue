<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useTheme, type Theme } from '@shared/composables/useTheme'
import { SunIcon, MoonIcon, ComputerDesktopIcon } from '@heroicons/vue/24/outline'

const { t } = useI18n()
const { theme, setTheme } = useTheme()

const icons = { light: SunIcon, dark: MoonIcon, system: ComputerDesktopIcon } as const

function cycle(): void {
  const order: Theme[] = ['light', 'dark', 'system']
  const idx = order.indexOf(theme.value)
  setTheme(order[(idx + 1) % order.length]!)
}
</script>

<template>
  <button
    type="button"
    class="theme-toggle"
    :aria-label="t('app.themeLabel', { theme })"
    @click="cycle"
  >
    <component
      :is="icons[theme]"
      class="theme-toggle__icon"
      aria-hidden="true"
    />
  </button>
</template>

<style scoped>
.theme-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  border: none;
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  transition: color var(--transition-fast), background var(--transition-fast);
}

.theme-toggle:hover {
  background: var(--color-surface);
  color: var(--color-fg);
}

.theme-toggle:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.theme-toggle__icon {
  width: 20px;
  height: 20px;
}
</style>
