<script setup lang="ts">
type Orientation = 'horizontal' | 'vertical'

const props = withDefaults(
  defineProps<{
    orientation?: Orientation
    label?: string
  }>(),
  {
    orientation: 'horizontal',
    label: undefined,
  },
)
</script>

<template>
  <div
    v-if="props.orientation === 'horizontal'"
    class="s-divider"
    :class="{ 's-divider--labeled': props.label }"
    role="separator"
    aria-orientation="horizontal"
  >
    <template v-if="props.label">
      <span class="s-divider__line" />
      <span class="s-divider__label">{{ props.label }}</span>
      <span class="s-divider__line" />
    </template>
  </div>
  <div
    v-else
    class="s-divider s-divider--vertical"
    role="separator"
    aria-orientation="vertical"
  />
</template>

<style scoped>
.s-divider {
  width: 100%;
  height: 1px;
  background-color: var(--color-border);
  border: none;
}

.s-divider--labeled {
  display: flex;
  align-items: center;
  gap: 8px;
  height: auto;
  background-color: transparent;
}

.s-divider__line {
  flex: 1;
  height: 1px;
  background-color: var(--color-border);
}

.s-divider__label {
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
  flex-shrink: 0;
}

.s-divider--vertical {
  width: 1px;
  height: 100%;
  background-color: var(--color-border);
}
</style>
