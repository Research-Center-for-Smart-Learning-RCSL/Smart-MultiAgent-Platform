<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'
import { ChevronRightIcon } from '@heroicons/vue/20/solid'

interface BreadcrumbItem {
  label: string
  to?: RouteLocationRaw
}

const props = defineProps<{
  items: BreadcrumbItem[]
}>()

const visibleItems = computed<(BreadcrumbItem | { ellipsis: true })[]>(() => {
  if (props.items.length <= 5) {
    return props.items
  }
  return [
    props.items[0],
    { ellipsis: true } as { ellipsis: true },
    ...props.items.slice(-3),
  ]
})

function isEllipsis(item: BreadcrumbItem | { ellipsis: true }): item is { ellipsis: true } {
  return 'ellipsis' in item
}
</script>

<template>
  <nav
    class="breadcrumb"
    aria-label="Breadcrumb"
  >
    <ol class="breadcrumb__list">
      <li
        v-for="(item, index) in visibleItems"
        :key="index"
        class="breadcrumb__item"
      >
        <ChevronRightIcon
          v-if="index > 0"
          class="breadcrumb__separator"
          aria-hidden="true"
        />
        <span
          v-if="isEllipsis(item)"
          class="breadcrumb__ellipsis"
        >...</span>
        <span
          v-else-if="index === visibleItems.length - 1"
          class="breadcrumb__current"
          aria-current="page"
        >{{ item.label }}</span>
        <RouterLink
          v-else-if="item.to"
          :to="item.to"
          class="breadcrumb__link"
        >
          {{ item.label }}
        </RouterLink>
        <span
          v-else
          class="breadcrumb__text"
        >{{ item.label }}</span>
      </li>
    </ol>
  </nav>
</template>

<style scoped>
.breadcrumb__list {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: 0.875rem;
}

.breadcrumb__item {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}

.breadcrumb__separator {
  width: 12px;
  height: 12px;
  color: var(--color-muted);
  flex-shrink: 0;
}

.breadcrumb__link {
  color: var(--color-accent);
  text-decoration: none;
  transition: color var(--transition-fast);
}

.breadcrumb__link:hover {
  color: var(--color-accent-hover);
  text-decoration: underline;
}

.breadcrumb__current {
  color: var(--color-fg);
  font-weight: 500;
}

.breadcrumb__ellipsis {
  color: var(--color-muted);
  user-select: none;
}

.breadcrumb__text {
  color: var(--color-muted);
}
</style>
