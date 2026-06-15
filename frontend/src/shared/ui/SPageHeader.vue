<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<template>
  <header class="flex items-center gap-3 mb-4">
    <slot name="prepend" />
    <div class="flex-1 min-w-0">
      <h1 class="text-xl font-semibold truncate">
        {{ title }}
      </h1>
      <p
        v-if="subtitle"
        class="text-sm text-muted"
      >
        {{ subtitle }}
      </p>
    </div>
    <slot />
  </header>
</template>
