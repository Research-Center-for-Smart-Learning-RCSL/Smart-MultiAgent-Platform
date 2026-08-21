<script setup lang="ts">
import { computed, type Component } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

const props = defineProps<{
  icon: Component
  label: string
  to: string
  // Match only the exact path. Set for list pages whose route is an ancestor of
  // other sidebar routes (e.g. `/projects`, an ancestor of every
  // `/projects/:id/*` section) so they don't stay highlighted alongside the
  // active section — otherwise two items read as active at once.
  exact?: boolean
}>()

const route = useRoute()

// Segment-boundary prefix match keeps a section highlighted on its detail routes
// (/projects/x/agents/y) without a shorter route matching a sibling that merely
// shares a string prefix (/foo vs /foobar). Root and `exact` links match only
// their own path.
const active = computed(() => {
  if (props.to === '/' || props.exact) return route.path === props.to
  return route.path === props.to || route.path.startsWith(props.to + '/')
})
</script>

<template>
  <RouterLink
    :to="to"
    class="nav-item"
    :class="{ 'nav-item--active': active }"
  >
    <component
      :is="icon"
      class="nav-icon"
    />
    <span class="nav-label">{{ label }}</span>
  </RouterLink>
</template>

<style scoped>
.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  height: 40px;
  padding: 0 var(--space-4);
  gap: var(--space-3);
  font-size: var(--font-size-sm);
  font-weight: var(--weight-normal);
  color: var(--color-sidebar-text);
  text-decoration: none;
  transition:
    background-color var(--transition-fast),
    color var(--transition-fast);
}

/* Active indicator: a pseudo-element bar that grows in with the activation
   instead of a static border, so switching items reads as motion. Using a
   pseudo-element (not border-left) also keeps icon alignment constant. */
.nav-item::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--color-sidebar-active-text);
  transform: scaleY(0);
  transition: transform var(--transition-fast);
}

.nav-item:hover {
  background-color: var(--color-sidebar-hover);
}

.nav-item--active {
  background-color: var(--color-sidebar-active-bg);
  color: var(--color-sidebar-active-text);
}

.nav-item--active::before {
  transform: scaleY(1);
}

.nav-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  color: inherit;
}

.nav-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
