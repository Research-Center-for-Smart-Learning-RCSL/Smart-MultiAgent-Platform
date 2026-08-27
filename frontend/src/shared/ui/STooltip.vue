<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch, type CSSProperties } from 'vue'

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
// Off-screen until the first measurement so the pre-position frame never
// paints anywhere visible.
const OFFSCREEN: CSSProperties = { top: '-9999px', left: '0px' }
const bubbleStyle = ref<CSSProperties>(OFFSCREEN)
const effectivePlacement = ref<Placement>(props.placement)
let delayTimer: ReturnType<typeof setTimeout> | null = null

// Gap between trigger and bubble, and the clearance kept from viewport edges.
const TRIGGER_GAP = 6
const VIEWPORT_MARGIN = 8
// Keeps the arrow off the bubble's rounded corners when the cross-axis clamp
// shifts the bubble away from the trigger's centre.
const ARROW_INSET = 10

const OPPOSITE: Record<Placement, Placement> = {
  top: 'bottom',
  bottom: 'top',
  left: 'right',
  right: 'left',
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max))
}

// Teleported + fixed so no ancestor overflow can clip the bubble (an in-place
// absolute bubble was cut off inside every overflow container it met: the
// chatroom header, the activity panel, table wraps). Flip to the opposite side
// when the preferred one lacks viewport room, clamp the cross axis, and point
// the arrow back at the trigger regardless of the clamp.
function updatePosition() {
  const trigger = triggerRef.value
  const bubble = bubbleRef.value
  if (!trigger || !bubble) return
  const rect = trigger.getBoundingClientRect()
  const bubbleWidth = bubble.offsetWidth
  const bubbleHeight = bubble.offsetHeight
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight

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

  let top: number
  let left: number
  if (placement === 'top' || placement === 'bottom') {
    top = placement === 'top' ? rect.top - TRIGGER_GAP - bubbleHeight : rect.bottom + TRIGGER_GAP
    left = clamp(
      rect.left + rect.width / 2 - bubbleWidth / 2,
      VIEWPORT_MARGIN,
      viewportWidth - VIEWPORT_MARGIN - bubbleWidth,
    )
  } else {
    left = placement === 'left' ? rect.left - TRIGGER_GAP - bubbleWidth : rect.right + TRIGGER_GAP
    top = clamp(
      rect.top + rect.height / 2 - bubbleHeight / 2,
      VIEWPORT_MARGIN,
      viewportHeight - VIEWPORT_MARGIN - bubbleHeight,
    )
  }

  const arrowOffset =
    placement === 'top' || placement === 'bottom'
      ? clamp(rect.left + rect.width / 2 - left, ARROW_INSET, bubbleWidth - ARROW_INSET)
      : clamp(rect.top + rect.height / 2 - top, ARROW_INSET, bubbleHeight - ARROW_INSET)

  bubbleStyle.value = {
    top: `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
    '--s-tooltip-arrow': `${Math.round(arrowOffset)}px`,
  }
}

function onScrollWhileVisible() {
  if (visible.value) updatePosition()
}

watch(visible, async (shown) => {
  if (shown) {
    window.addEventListener('scroll', onScrollWhileVisible, { capture: true, passive: true })
    window.addEventListener('resize', onScrollWhileVisible, { passive: true })
    await nextTick()
    updatePosition()
  } else {
    window.removeEventListener('scroll', onScrollWhileVisible, { capture: true })
    window.removeEventListener('resize', onScrollWhileVisible)
    bubbleStyle.value = OFFSCREEN
  }
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
  window.removeEventListener('scroll', onScrollWhileVisible, { capture: true })
  window.removeEventListener('resize', onScrollWhileVisible)
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
  /* Coordinates come from updatePosition; declared fixed here so the bubble is
     never laid out in body flow on the frame before it is measured. */
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
