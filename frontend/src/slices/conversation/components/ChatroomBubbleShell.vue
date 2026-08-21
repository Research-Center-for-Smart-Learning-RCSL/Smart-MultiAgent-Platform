<template>
  <article
    class="bubble"
    :class="{ 'bubble--agent': agent }"
  >
    <header class="bubble__meta">
      <slot name="meta" />
    </header>
    <slot />
  </article>
</template>

<script setup lang="ts">
// Shared message-bubble frame for ChatroomMessageBubble + ChatroomStreamingBubble
// so the bubble surface/border/meta layout lives in one place. Slotted content
// keeps the caller's scope, so per-variant styling stays in each component.
defineProps<{
  agent?: boolean
}>()
</script>

<style scoped>
.bubble {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  max-width: 75%;
  min-width: 200px;
}

.bubble--agent {
  background: var(--color-surface);
  border-left: 3px solid var(--color-accent);
  max-width: 85%;
}

.bubble__meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-1-5);
}
</style>
