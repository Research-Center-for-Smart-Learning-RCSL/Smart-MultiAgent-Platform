<script setup lang="ts">
import { reactive } from 'vue'
import { ChevronRightIcon } from '@heroicons/vue/20/solid'

export interface AccordionItem {
  key: string
  title: string
  defaultOpen?: boolean
}

const props = withDefaults(
  defineProps<{
    items: AccordionItem[]
    multiple?: boolean
  }>(),
  {
    multiple: false,
  },
)

const openSet = reactive(
  new Set<string>(
    props.items.filter((item) => item.defaultOpen).map((item) => item.key),
  ),
)

function toggle(key: string) {
  if (openSet.has(key)) {
    openSet.delete(key)
  } else {
    if (!props.multiple) {
      openSet.clear()
    }
    openSet.add(key)
  }
}

function isOpen(key: string): boolean {
  return openSet.has(key)
}
</script>

<template>
  <div class="s-accordion">
    <div
      v-for="item in items"
      :key="item.key"
      class="s-accordion__item"
    >
      <button
        type="button"
        class="s-accordion__header"
        :aria-expanded="isOpen(item.key)"
        @click="toggle(item.key)"
        @keydown.enter.prevent="toggle(item.key)"
        @keydown.space.prevent="toggle(item.key)"
      >
        <slot :name="`header-${item.key}`">
          <span class="s-accordion__title">{{ item.title }}</span>
        </slot>
        <ChevronRightIcon
          class="s-accordion__chevron"
          :class="{ 's-accordion__chevron--open': isOpen(item.key) }"
          aria-hidden="true"
        />
      </button>
      <div
        class="s-accordion__panel"
        :class="{ 's-accordion__panel--open': isOpen(item.key) }"
      >
        <div class="s-accordion__panel-inner">
          <slot :name="`item-${item.key}`" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.s-accordion {
  width: 100%;
}

.s-accordion__item {
  border-bottom: 1px solid var(--color-border);
}

.s-accordion__header {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 44px;
  padding: 0 16px;
  border: none;
  background: none;
  cursor: pointer;
  color: var(--color-fg);
  font-size: 14px;
  font-weight: 500;
  text-align: left;
}

.s-accordion__header:hover {
  background: var(--color-surface);
}

.s-accordion__header:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.s-accordion__title {
  flex: 1;
  min-width: 0;
}

.s-accordion__chevron {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  color: var(--color-muted);
  transform: rotate(0deg);
  transition: transform 200ms ease;
}

.s-accordion__chevron--open {
  transform: rotate(90deg);
}

/* Slide-down via grid-rows trick */
.s-accordion__panel {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 200ms ease;
}

.s-accordion__panel--open {
  grid-template-rows: 1fr;
}

.s-accordion__panel-inner {
  overflow: hidden;
  padding: 0 16px;
}

.s-accordion__panel--open .s-accordion__panel-inner {
  padding: 16px;
}
</style>
