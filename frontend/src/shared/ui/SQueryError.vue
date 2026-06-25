<script setup lang="ts">
import SAlert from './SAlert.vue'
import SButton from './SButton.vue'

defineProps<{
  message: string
  retryLabel: string
}>()

defineEmits<{
  retry: []
}>()
</script>

<template>
  <SAlert variant="danger">
    {{ message }}
    <template #actions>
      <SButton
        size="sm"
        variant="secondary"
        @click="$emit('retry')"
      >
        {{ retryLabel }}
      </SButton>
    </template>
  </SAlert>
</template>
