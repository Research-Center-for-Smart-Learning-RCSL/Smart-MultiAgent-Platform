<script setup lang="ts">
import { ref, watch } from 'vue'
import { ChevronRightIcon } from '@heroicons/vue/20/solid'

const props = withDefaults(defineProps<{
  label: string
  storageKey: string
  defaultCollapsed?: boolean
}>(), {
  defaultCollapsed: false,
})

const STORAGE_PREFIX = 'smap:sidebar:'

function loadState(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_PREFIX + props.storageKey)
    if (stored !== null) return stored === '1'
  } catch { /* private browsing */ }
  return props.defaultCollapsed
}

const collapsed = ref(loadState())

watch(collapsed, (val) => {
  try {
    localStorage.setItem(STORAGE_PREFIX + props.storageKey, val ? '1' : '0')
  } catch { /* ignore */ }
})

function toggle() {
  collapsed.value = !collapsed.value
}
</script>

<template>
  <div class="sidebar-group">
    <button
      class="group-header"
      type="button"
      :aria-expanded="!collapsed"
      @click="toggle"
    >
      <ChevronRightIcon
        class="group-chevron"
        :class="{ 'group-chevron--open': !collapsed }"
      />
      <span class="group-label">{{ label }}</span>
    </button>
    <div
      class="group-content"
      :class="{ 'group-content--collapsed': collapsed }"
    >
      <div class="group-content__inner">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
.group-header {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 8px 12px;
  gap: 4px;
  border: none;
  background: none;
  cursor: pointer;
  text-transform: uppercase;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-sidebar-section-text);
  letter-spacing: 0.05em;
}

.group-header:hover {
  color: var(--color-sidebar-text);
}

.group-chevron {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  transition: transform var(--transition-fast);
}

.group-chevron--open {
  transform: rotate(90deg);
}

.group-label {
  user-select: none;
}

.group-content {
  display: grid;
  grid-template-rows: 1fr;
  transition: grid-template-rows var(--transition-normal);
}

.group-content--collapsed {
  grid-template-rows: 0fr;
}

.group-content__inner {
  overflow: hidden;
}
</style>
