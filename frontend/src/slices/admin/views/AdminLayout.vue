<script setup lang="ts">
import { useBreakpoint } from '@shared/composables'
import AdminNav from '../components/AdminNav.vue'

// At lg+ the nav is a left sidebar beside the content; below that it stacks
// on top (horizontal tabs on tablet, a dropdown selector on mobile).
const { isDesktop } = useBreakpoint()
</script>

<template>
  <div
    class="admin-layout"
    :class="{ 'admin-layout--stacked': !isDesktop }"
  >
    <aside class="admin-layout__nav">
      <AdminNav />
    </aside>
    <div class="admin-layout__content">
      <router-view />
    </div>
  </div>
</template>

<style scoped>
.admin-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 1.5rem;
  align-items: start;
}

.admin-layout--stacked {
  grid-template-columns: minmax(0, 1fr);
  gap: 1rem;
}

.admin-layout__content {
  min-width: 0;
}
</style>
