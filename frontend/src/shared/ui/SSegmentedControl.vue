<script setup lang="ts">
export interface SegmentedOption {
  value: string
  label: string
  disabled?: boolean
  title?: string
  testId?: string
}

withDefaults(
  defineProps<{
    options: SegmentedOption[]
    accessibleLabel: string
    size?: 'sm' | 'md'
  }>(),
  { size: 'sm' },
)

const model = defineModel<string>({ required: true })

function select(option: SegmentedOption): void {
  if (!option.disabled) model.value = option.value
}
</script>

<template>
  <div
    class="s-segmented-control"
    :class="`s-segmented-control--${size}`"
    role="group"
    :aria-label="accessibleLabel"
  >
    <button
      v-for="(option, index) in options"
      :key="option.value"
      type="button"
      class="s-segmented-control__option"
      :class="{
        's-segmented-control__option--selected': model === option.value,
        's-segmented-control__option--first': index === 0,
        's-segmented-control__option--last': index === options.length - 1,
      }"
      :aria-pressed="model === option.value"
      :disabled="option.disabled"
      :title="option.title"
      :data-testid="option.testId"
      @click="select(option)"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<style scoped>
.s-segmented-control {
  display: inline-flex;
  width: fit-content;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.s-segmented-control__option {
  min-width: max(var(--control-h-sm), 44px);
  min-height: max(var(--control-h-sm), 44px);
  padding: var(--space-1) var(--space-3);
  border: 0;
  background: var(--color-bg);
  color: var(--color-fg);
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  cursor: pointer;
}

.s-segmented-control--md .s-segmented-control__option {
  min-height: max(var(--control-h-md), 44px);
}

.s-segmented-control__option + .s-segmented-control__option {
  border-left: 1px solid var(--color-border-subtle);
}

.s-segmented-control__option--selected {
  background: var(--color-accent);
  color: var(--color-on-accent);
}

.s-segmented-control__option--first {
  border-top-left-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
}

.s-segmented-control__option--last {
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
}

.s-segmented-control__option--first.s-segmented-control__option--last {
  border-radius: var(--radius-md);
}

.s-segmented-control__option:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
</style>
