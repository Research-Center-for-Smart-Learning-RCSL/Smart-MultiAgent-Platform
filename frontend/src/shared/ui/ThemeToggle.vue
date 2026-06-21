<script setup lang="ts">
import { useTheme, type Theme } from '@shared/composables/useTheme'
import { SunIcon, MoonIcon, ComputerDesktopIcon } from '@heroicons/vue/24/outline'

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
    :aria-label="'Theme: ' + theme"
    @click="cycle"
  >
    <component
      :is="icons[theme]"
      class="w-5 h-5"
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
  border-radius: var(--radius-full);
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-fg);
  cursor: pointer;
}
.theme-toggle:hover {
  background: var(--color-border);
}
</style>
