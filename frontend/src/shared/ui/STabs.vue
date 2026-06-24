<script setup lang="ts">
import { ref, type Component } from 'vue'

interface TabItem {
  key: string
  label: string
  icon?: Component
  badge?: string | number
  disabled?: boolean
}

const props = defineProps<{
  modelValue: string
  tabs: TabItem[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const tabRefs = ref<HTMLElement[]>([])

function setTabRef(el: unknown, index: number) {
  if (el instanceof HTMLElement) {
    tabRefs.value[index] = el
  }
}

function selectTab(key: string) {
  emit('update:modelValue', key)
}

function getEnabledTabs(): number[] {
  return props.tabs
    .map((tab, i) => ({ i, disabled: tab.disabled }))
    .filter((t) => !t.disabled)
    .map((t) => t.i)
}

function onKeydown(e: KeyboardEvent) {
  const enabled = getEnabledTabs()
  if (enabled.length === 0) return

  const currentIndex = props.tabs.findIndex((t) => t.key === props.modelValue)
  const currentPos = enabled.indexOf(currentIndex)
  let nextPos = currentPos

  if (e.key === 'ArrowRight') {
    e.preventDefault()
    nextPos = currentPos < enabled.length - 1 ? currentPos + 1 : 0
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    nextPos = currentPos > 0 ? currentPos - 1 : enabled.length - 1
  } else if (e.key === 'Home') {
    e.preventDefault()
    nextPos = 0
  } else if (e.key === 'End') {
    e.preventDefault()
    nextPos = enabled.length - 1
  } else {
    return
  }

  const nextIndex = enabled[nextPos]
  const nextTab = props.tabs[nextIndex]
  selectTab(nextTab.key)
  tabRefs.value[nextIndex]?.focus()
}
</script>

<template>
  <div class="s-tabs">
    <div
      class="s-tabs__list"
      role="tablist"
      tabindex="-1"
      @keydown="onKeydown"
    >
      <button
        v-for="(tab, index) in tabs"
        :key="tab.key"
        :ref="(el) => setTabRef(el, index)"
        role="tab"
        type="button"
        class="s-tabs__tab"
        :class="{
          's-tabs__tab--active': modelValue === tab.key,
          's-tabs__tab--disabled': tab.disabled,
        }"
        :aria-selected="modelValue === tab.key"
        :aria-controls="`tabpanel-${tab.key}`"
        :tabindex="modelValue === tab.key ? 0 : -1"
        :disabled="tab.disabled"
        @click="!tab.disabled && selectTab(tab.key)"
      >
        <component
          :is="tab.icon"
          v-if="tab.icon"
          class="s-tabs__icon"
        />
        <span>{{ tab.label }}</span>
        <span
          v-if="tab.badge != null"
          class="s-tabs__badge"
        >
          {{ tab.badge }}
        </span>
      </button>
    </div>
    <div class="s-tabs__panels">
      <div
        v-for="tab in tabs"
        :id="`tabpanel-${tab.key}`"
        :key="tab.key"
        role="tabpanel"
        :aria-labelledby="tab.key"
        :hidden="modelValue !== tab.key"
      >
        <slot
          v-if="modelValue === tab.key"
          :name="`tab-${tab.key}`"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.s-tabs {
  display: flex;
  flex-direction: column;
}

.s-tabs__list {
  display: flex;
  border-bottom: 1px solid var(--color-border);
}

.s-tabs__tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  padding: 0 16px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  color: var(--color-muted);
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition:
    color var(--transition-fast),
    border-color var(--transition-fast);
}

.s-tabs__tab:hover:not(.s-tabs__tab--active):not(.s-tabs__tab--disabled) {
  color: var(--color-fg);
}

.s-tabs__tab--active {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
}

.s-tabs__tab--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.s-tabs__tab:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.s-tabs__icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.s-tabs__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 6px;
  min-width: 18px;
  height: 18px;
  font-size: 0.7rem;
  font-weight: 600;
  line-height: 1;
  border-radius: var(--radius-full);
  background: var(--color-surface);
  color: var(--color-muted);
}

.s-tabs__panels {
  margin-top: 0;
}
</style>
