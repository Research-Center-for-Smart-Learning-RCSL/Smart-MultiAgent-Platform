<script setup lang="ts">
defineProps<{
  title?: string
  // Forwarded to the heading so forms inside the card can point
  // aria-labelledby at it.
  titleId?: string
}>()
</script>

<template>
  <div class="s-auth-card">
    <div class="s-auth-card__body">
      <h1
        v-if="title"
        class="s-auth-card__title"
        v-bind="titleId ? { id: titleId } : {}"
      >
        {{ title }}
      </h1>
      <slot />
    </div>
    <p
      v-if="$slots.footer"
      class="s-auth-card__footer"
    >
      <slot name="footer" />
    </p>
  </div>
</template>

<style scoped>
.s-auth-card__body {
  background: var(--color-bg);
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-2);
  padding: var(--space-8);
}

.s-auth-card__title {
  font-size: var(--font-size-2xl);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin: 0 0 var(--space-6);
}

.s-auth-card__footer {
  text-align: center;
  color: var(--color-muted);
  font-size: var(--font-size-sm);
  margin: var(--space-6) 0 0;
}

.s-auth-card__footer :deep(a) {
  color: var(--color-accent);
  text-decoration: none;
  font-weight: var(--weight-medium);
}

.s-auth-card__footer :deep(a:hover) {
  text-decoration: underline;
}

@media (max-width: 480px) {
  .s-auth-card__body {
    border-radius: 0;
    box-shadow: none;
    padding: var(--space-6) 0;
  }
}
</style>
