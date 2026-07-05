<script setup lang="ts">
defineProps<{
  variant?: 'default' | 'elevated' | 'bordered' | 'flat'
  padding?: 'none' | 'sm' | 'md' | 'lg'
  // Opt-in hover treatment for cards that act as links or triggers: a small
  // lift plus one elevation step, per the motion language (R24.49).
  hoverable?: boolean
}>()
</script>

<template>
  <div
    class="s-card"
    :class="[
      `s-card--${variant ?? 'default'}`,
      `s-card--pad-${padding ?? 'md'}`,
      { 's-card--hoverable': hoverable },
    ]"
  >
    <div
      v-if="$slots.header"
      class="s-card__header"
    >
      <slot name="header" />
    </div>
    <slot />
    <div
      v-if="$slots.footer"
      class="s-card__footer"
    >
      <slot name="footer" />
    </div>
  </div>
</template>

<style scoped>
.s-card {
  border-radius: var(--radius-lg);
}

.s-card--default,
.s-card--bordered {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  box-shadow: var(--elevation-0);
}

/* A transparent border keeps the box the same size as bordered variants, so
   swapping variants never shifts layout. */
.s-card--elevated {
  background: var(--color-bg);
  border: 1px solid transparent;
  box-shadow: var(--elevation-1);
}

.s-card--flat {
  background: var(--color-surface);
}

.s-card--hoverable {
  transition:
    transform var(--transition-normal),
    box-shadow var(--transition-normal),
    border-color var(--transition-normal);
}

.s-card--hoverable:hover {
  transform: translateY(calc(var(--motion-lift) * -1));
  box-shadow: var(--elevation-2);
}

/* --card-pad mirrors the padding so header/footer can bleed to the card edge
   with negative margins regardless of which padding size is active. */
.s-card--pad-none {
  --card-pad: 0px;
  padding: 0;
}
.s-card--pad-sm {
  --card-pad: var(--space-3);
  padding: var(--space-3);
}
.s-card--pad-md {
  --card-pad: var(--space-4);
  padding: var(--space-4);
}
.s-card--pad-lg {
  --card-pad: var(--space-6);
  padding: var(--space-6);
}

.s-card__header {
  margin: calc(var(--card-pad) * -1) calc(var(--card-pad) * -1) var(--card-pad);
  padding: var(--space-3) max(var(--card-pad), var(--space-4));
  border-bottom: 1px solid var(--color-border);
  font-size: var(--font-size-md);
  font-weight: var(--weight-semibold);
}

.s-card__footer {
  margin: var(--card-pad) calc(var(--card-pad) * -1) calc(var(--card-pad) * -1);
  padding: var(--space-3) max(var(--card-pad), var(--space-4));
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: 0 0 var(--radius-lg) var(--radius-lg);
}
</style>
