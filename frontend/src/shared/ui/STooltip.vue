<script setup lang="ts">
import { onBeforeUnmount, ref, type CSSProperties } from 'vue'
import {
  clampToViewport,
  useAnchoredPosition,
  VIEWPORT_MARGIN,
  type AnchoredPositionContext,
} from '@shared/composables/useAnchoredPosition'

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
const triggerRef = ref<HTMLElement | null>(null)
const bubbleRef = ref<HTMLElement | null>(null)
const effectivePlacement = ref<Placement>(props.placement)
let delayTimer: ReturnType<typeof setTimeout> | null = null

// Gap between trigger and bubble.
const TRIGGER_GAP = 6
// Keeps the arrow off the bubble's rounded corners when the cross-axis clamp
// shifts the bubble away from the trigger's centre.
const ARROW_INSET = 10

const OPPOSITE: Record<Placement, Placement> = {
  top: 'bottom',
  bottom: 'top',
  left: 'right',
  right: 'left',
}

// Teleported + fixed so no ancestor overflow can clip the bubble (an in-place
// absolute bubble was cut off inside every overflow container it met: the
// chatroom header, the activity panel, table wraps). Flip to the opposite side
// when the preferred one lacks viewport room, clamp both axes, and point the
// arrow back at the trigger regardless of the clamps.
function computePosition(ctx: AnchoredPositionContext): CSSProperties {
  const { rect, panel, viewportWidth, viewportHeight } = ctx
  const bubbleWidth = panel.offsetWidth
  const bubbleHeight = panel.offsetHeight

  const room: Record<Placement, number> = {
    top: rect.top - TRIGGER_GAP - VIEWPORT_MARGIN,
    bottom: viewportHeight - rect.bottom - TRIGGER_GAP - VIEWPORT_MARGIN,
    left: rect.left - TRIGGER_GAP - VIEWPORT_MARGIN,
    right: viewportWidth - rect.right - TRIGGER_GAP - VIEWPORT_MARGIN,
  }
  let placement = props.placement
  const needed = placement === 'top' || placement === 'bottom' ? bubbleHeight : bubbleWidth
  if (needed > room[placement] && room[OPPOSITE[placement]] > room[placement]) {
    placement = OPPOSITE[placement]
  }
  effectivePlacement.value = placement

  // Both axes are clamped: the flip only picks the better side, and when
  // neither side has room an unclamped main axis would push the bubble's
  // first lines off screen.
  let top: number
  let left: number
  if (placement === 'top' || placement === 'bottom') {
    top = placement === 'top' ? rect.top - TRIGGER_GAP - bubbleHeight : rect.bottom + TRIGGER_GAP
    top = clampToViewport(top, bubbleHeight, viewportHeight)
    left = clampToViewport(
      rect.left + rect.width / 2 - bubbleWidth / 2,
      bubbleWidth,
      viewportWidth,
    )
  } else {
    left = placement === 'left' ? rect.left - TRIGGER_GAP - bubbleWidth : rect.right + TRIGGER_GAP
    left = clampToViewport(left, bubbleWidth, viewportWidth)
    top = clampToViewport(
      rect.top + rect.height / 2 - bubbleHeight / 2,
      bubbleHeight,
      viewportHeight,
    )
  }

  const arrowOffset =
    placement === 'top' || placement === 'bottom'
      ? Math.min(Math.max(rect.left + rect.width / 2 - left, ARROW_INSET), bubbleWidth - ARROW_INSET)
      : Math.min(Math.max(rect.top + rect.height / 2 - top, ARROW_INSET), bubbleHeight - ARROW_INSET)

  return {
    top: `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
    '--s-tooltip-arrow': `${Math.round(arrowOffset)}px`,
  }
}

const { style: bubbleStyle } = useAnchoredPosition({
  anchor: triggerRef,
  panel: bubbleRef,
  open: visible,
  compute: computePosition,
  // Scrolling fires no mouseleave until the mouse moves, so without this a
  // bubble whose trigger scrolled out of its container keeps floating over
  // unrelated chrome, pointing at nothing.
  onAnchorClipped: hideTooltip,
})

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

onBeforeUnmount(() => {
  if (delayTimer) clearTimeout(delayTimer)
})
</script>

<template>
  <span
    ref="triggerRef"
    class="s-tooltip-trigger"
    role="none"
    @mouseenter="showTooltip(false)"
    @mouseleave="hideTooltip()"
    @focusin="showTooltip(true)"
    @focusout="hideTooltip()"
  >
    <slot />
    <Teleport to="body">
      <div
        v-show="visible"
        ref="bubbleRef"
        class="s-tooltip"
        :class="`s-tooltip--${effectivePlacement}`"
        :style="bubbleStyle"
        role="tooltip"
      >
        {{ props.content }}
      </div>
    </Teleport>
  </span>
</template>

<style scoped>
.s-tooltip-trigger {
  display: inline-flex;
}

.s-tooltip {
  /* Coordinates come from useAnchoredPosition; declared fixed here so the
     bubble is never laid out in body flow on the frame before it is
     measured. */
  position: fixed;
  z-index: var(--z-tooltip);
  font-size: var(--font-size-xs);
  line-height: var(--line-snug);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  background-color: var(--color-fg);
  color: var(--color-bg);
  box-shadow: var(--shadow-md);
  /* The bubble sizes from its text; the cap makes long content wrap instead of
     overflowing (nowrap + max-width just let it spill out of the bubble). */
  width: max-content;
  white-space: normal;
  overflow-wrap: break-word;
  pointer-events: none;
  max-width: 240px;
}

/* The arrow tracks the trigger's centre via --s-tooltip-arrow, so it still
   points at the trigger when the viewport clamp shifts the bubble sideways. */
.s-tooltip--top::after {
  content: '';
  position: absolute;
  top: 100%;
  left: var(--s-tooltip-arrow, 50%);
  transform: translateX(-50%);
  border: 4px solid transparent;
  border-top-color: var(--color-fg);
}

.s-tooltip--bottom::after {
  content: '';
  position: absolute;
  bottom: 100%;
  left: var(--s-tooltip-arrow, 50%);
  transform: translateX(-50%);
  border: 4px solid transparent;
  border-bottom-color: var(--color-fg);
}

.s-tooltip--left::after {
  content: '';
  position: absolute;
  left: 100%;
  top: var(--s-tooltip-arrow, 50%);
  transform: translateY(-50%);
  border: 4px solid transparent;
  border-left-color: var(--color-fg);
}

.s-tooltip--right::after {
  content: '';
  position: absolute;
  right: 100%;
  top: var(--s-tooltip-arrow, 50%);
  transform: translateY(-50%);
  border: 4px solid transparent;
  border-right-color: var(--color-fg);
}
</style>
