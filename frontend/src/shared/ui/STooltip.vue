<script setup lang="ts">
import { ref } from 'vue'

type Placement = 'top' | 'bottom' | 'left' | 'right'

const props = withDefaults(
  defineProps<{
    content: string
    placement?: Placement
    delay?: number
  }>(),
  {
    placement: 'top',
    delay: 300,
  },
)

const visible = ref(false)
let delayTimer: ReturnType<typeof setTimeout> | null = null

function showTooltip(immediate = false) {
  if (delayTimer) {
    clearTimeout(delayTimer)
    delayTimer = null
  }
  if (immediate) {
    visible.value = true
  } else {
    delayTimer = setTimeout(() => {
      visible.value = true
    }, props.delay)
  }
}

function hideTooltip() {
  if (delayTimer) {
    clearTimeout(delayTimer)
    delayTimer = null
  }
  visible.value = false
}
</script>

<template>
  <span
    class="s-tooltip-trigger"
    role="none"
    @mouseenter="showTooltip(false)"
    @mouseleave="hideTooltip()"
    @focusin="showTooltip(true)"
    @focusout="hideTooltip()"
  >
    <slot />
    <div
      v-show="visible"
      class="s-tooltip"
      :class="`s-tooltip--${props.placement}`"
      role="tooltip"
    >
      {{ props.content }}
    </div>
  </span>
</template>

<style scoped>
.s-tooltip-trigger {
  position: relative;
  display: inline-flex;
}

.s-tooltip {
  position: absolute;
  z-index: var(--z-tooltip);
  font-size: var(--font-size-xs);
  line-height: var(--line-snug);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  background-color: var(--color-fg);
  color: var(--color-bg);
  box-shadow: var(--shadow-md);
  /* The containing block is the tiny trigger span, so shrink-to-fit would wrap
     the text at the trigger's width; max-content sizes from the text instead,
     and the cap below makes long content wrap rather than overflow the bubble
     (nowrap + max-width just let it spill out). */
  width: max-content;
  white-space: normal;
  overflow-wrap: break-word;
  pointer-events: none;
  max-width: 240px;
}

/* Placement: top */
.s-tooltip--top {
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-bottom: var(--space-1-5);
}
.s-tooltip--top::after {
  content: '';
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  border: 4px solid transparent;
  border-top-color: var(--color-fg);
}

/* Placement: bottom */
.s-tooltip--bottom {
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-top: var(--space-1-5);
}
.s-tooltip--bottom::after {
  content: '';
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  border: 4px solid transparent;
  border-bottom-color: var(--color-fg);
}

/* Placement: left */
.s-tooltip--left {
  right: 100%;
  top: 50%;
  transform: translateY(-50%);
  margin-right: var(--space-1-5);
}
.s-tooltip--left::after {
  content: '';
  position: absolute;
  left: 100%;
  top: 50%;
  transform: translateY(-50%);
  border: 4px solid transparent;
  border-left-color: var(--color-fg);
}

/* Placement: right */
.s-tooltip--right {
  left: 100%;
  top: 50%;
  transform: translateY(-50%);
  margin-left: var(--space-1-5);
}
.s-tooltip--right::after {
  content: '';
  position: absolute;
  right: 100%;
  top: 50%;
  transform: translateY(-50%);
  border: 4px solid transparent;
  border-right-color: var(--color-fg);
}
</style>
