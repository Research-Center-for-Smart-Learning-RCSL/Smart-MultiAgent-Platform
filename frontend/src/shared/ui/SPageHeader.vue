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

/* The actions may not eat the title.

   `__actions` is `flex-shrink: 0` and `__content` could shrink to nothing, so a
   narrow viewport resolved the row entirely in the actions' favour: at 375px
   `/projects/:id/agents` rendered its `AI Agents` title in 13px of box - the
   single letter `A` - and `/orgs/:id` rendered it in 0, with the action row
   itself running off the right edge. The row had no `flex-wrap`, so there was
   nowhere for the overflow to go.

   The floor is what forces the wrap: once `__content` cannot shrink past
   `--page-header-title-min`, the actions no longer fit beside it and move to a
   line of their own, where the title gets the full width back. It is a `min()`
   against 100% so a container narrower than the floor still yields rather than
   overflowing. */
.s-page-header__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.s-page-header__content {
  flex: 1;
  /* Not `min-width: 0`. That is what let the box reach 0px; ellipsis still
     works, because `overflow: hidden` on the title does not depend on it. */
  min-width: min(100%, var(--page-header-title-min));
}

.s-page-header__title {
  font-size: var(--font-size-2xl);
  font-weight: var(--weight-semibold);
  line-height: var(--line-tight);
  /* Restated rather than inherited from @layer base: this is the one heading
     that truncates (below), so where it ellipsises depends on its tracking. */
  letter-spacing: var(--tracking-tight);
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

/* Wraps for the same reason the row does: `/orgs/:id` carries five actions,
   which exceed a 375px line on their own.

   `max-width: 100%` is what makes the wrap reachable. `flex-shrink: 0` keeps
   this box at its max-content width no matter how narrow the line is, so
   `flex-wrap` alone never fired - the box simply stayed 642px wide inside a
   375px viewport and the last two buttons were unreachable past the edge. The
   cap bounds it to the line; the wrap then does its job inside that. The
   `flex-shrink: 0` stays, because on a wide row it is still what stops a long
   title from eating the buttons. */
.s-page-header__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  max-width: 100%;
}
</style>
