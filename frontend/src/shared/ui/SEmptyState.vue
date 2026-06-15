<script setup lang="ts">
import type { Component } from 'vue'

defineProps<{
  message: string
  icon?: Component
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center py-12 text-muted">
    <component
      :is="icon"
      v-if="icon"
      class="w-10 h-10 mb-3 opacity-40"
    />
    <p class="text-sm">
      {{ message }}
    </p>
    <slot />
  </div>
</template>
