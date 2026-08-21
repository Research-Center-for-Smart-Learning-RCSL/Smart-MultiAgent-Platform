<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'
import { RouterLink } from 'vue-router'
import { ChevronRightIcon } from '@heroicons/vue/20/solid'

defineProps<{
  title: string
  subtitle?: string
  breadcrumbs?: Array<{ label: string; to?: RouteLocationRaw }>
}>()
</script>

<template>
  <header class="s-page-header">
    <nav
      v-if="breadcrumbs?.length"
      class="s-page-header__breadcrumbs"
    >
      <template
        v-for="(crumb, i) in breadcrumbs"
        :key="i"
      >
        <ChevronRightIcon
          v-if="i > 0"
          class="s-page-header__separator"
          aria-hidden="true"
        />
        <RouterLink
          v-if="crumb.to && i < breadcrumbs.length - 1"
          :to="crumb.to"
          class="s-page-header__crumb-link"
        >
          {{ crumb.label }}
        </RouterLink>
        <span
          v-else
          class="s-page-header__crumb-current"
        >
          {{ crumb.label }}
        </span>
      </template>
    </nav>
    <div class="s-page-header__row">
      <slot name="prepend" />
      <div class="s-page-header__content">
        <h1 class="s-page-header__title">
          {{ title }}
        </h1>
        <p
          v-if="subtitle"
          class="s-page-header__subtitle"
        >
          {{ subtitle }}
        </p>
        <div
          v-if="$slots.description"
          class="s-page-header__description"
        >
          <slot name="description" />
        </div>
      </div>
      <div
        v-if="$slots.actions"
        class="s-page-header__actions"
      >
        <slot name="actions" />
      </div>
      <slot />
    </div>
  </header>
</template>

<style scoped>
.s-page-header {
  margin-bottom: var(--space-4);
}

.s-page-header__breadcrumbs {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin-bottom: var(--space-2);
  font-size: var(--font-size-sm);
}

.s-page-header__separator {
  width: 12px;
  height: 12px;
  color: var(--color-muted);
  flex-shrink: 0;
}

.s-page-header__crumb-link {
  color: var(--color-accent);
  text-decoration: none;
}

.s-page-header__crumb-link:hover {
  text-decoration: underline;
}

.s-page-header__crumb-current {
  color: var(--color-muted);
}

.s-page-header__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.s-page-header__content {
  flex: 1;
  min-width: 0;
}

.s-page-header__title {
  font-size: var(--font-size-2xl);
  font-weight: var(--weight-semibold);
  line-height: var(--line-snug);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.s-page-header__subtitle {
  font-size: var(--font-size-sm);
  color: var(--color-muted);
  margin: var(--space-0-5) 0 0;
}

.s-page-header__description {
  font-size: var(--font-size-sm);
  color: var(--color-muted);
  margin-top: var(--space-1);
}

.s-page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}
</style>
