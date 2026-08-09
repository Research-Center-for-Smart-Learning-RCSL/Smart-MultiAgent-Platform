<script setup lang="ts">
// A focusable window-splitter. Controlled: it never holds the width itself, it
// reports the width the pointer or keyboard is asking for and re-renders from
// whatever the parent clamps that to — which is why `aria-valuenow` always
// reflects the applied width rather than the raw gesture.
//
// The root is a `<button>` carrying `role="separator"`: the focusable-separator
// pattern needs a natively interactive, natively focusable element, and re-roling
// a button is the standard way to get one without hand-rolling focus behaviour.
// Keep it as the template's only root node — a sibling comment would make the
// component multi-root, and attribute/listener fallthrough would silently stop.

import { onBeforeUnmount, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    value: number
    min: number
    max: number
    /** Accessible name; the caller supplies it already translated. */
    label: string
    step?: number
    largeStep?: number
    /** Pointer moves left increase the value. Set for a panel on the right edge,
     *  whose leading edge is the thing being dragged. */
    invert?: boolean
  }>(),
  { step: 16, largeStep: 64, invert: false },
)

const emit = defineEmits<{
  'update:value': [value: number]
  reset: []
}>()

const dragging = ref(false)
let startX = 0
let startValue = 0

function setDocumentDragState(active: boolean): void {
  if (typeof document === 'undefined') return
  // Applied on the document, not the handle: during a drag the pointer travels
  // across the whole page, and a text selection or an I-beam cursor picked up
  // from the elements it passes over reads as a broken gesture.
  document.body.style.userSelect = active ? 'none' : ''
  document.body.style.cursor = active ? 'col-resize' : ''
}

function onPointerDown(event: PointerEvent): void {
  if (event.button !== 0) return
  dragging.value = true
  startX = event.clientX
  startValue = props.value
  setDocumentDragState(true)
  // Optional-called: pointer capture is not universally implemented, and losing
  // it degrades the drag to "until the pointer leaves the element" rather than
  // breaking it.
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  event.preventDefault()
}

function onPointerMove(event: PointerEvent): void {
  if (!dragging.value) return
  const delta = event.clientX - startX
  // Resolved against the value captured at drag start rather than accumulated
  // per move, so dragging past a bound and back does not desynchronise the
  // handle from the pointer.
  emit('update:value', startValue + (props.invert ? -delta : delta))
}

// Also the `lostpointercapture` handler: the browser can revoke capture without
// a pointerup or pointercancel (a context menu, an overlay stealing the pointer),
// which would otherwise leave the drag live -- the page unselectable, the resize
// cursor stuck, and a button-less pointer move still resizing. Re-entrant by
// design, since releasing capture below fires the event again.
function endDrag(event: PointerEvent): void {
  if (!dragging.value) return
  dragging.value = false
  setDocumentDragState(false)
  const target = event.currentTarget as HTMLElement
  if (target.hasPointerCapture?.(event.pointerId)) target.releasePointerCapture?.(event.pointerId)
}

function onKeydown(event: KeyboardEvent): void {
  const large = event.shiftKey
  const stepBy = large ? props.largeStep : props.step

  if (event.key === 'ArrowLeft') {
    emit('update:value', props.value + (props.invert ? stepBy : -stepBy))
  } else if (event.key === 'ArrowRight') {
    emit('update:value', props.value + (props.invert ? -stepBy : stepBy))
  } else if (event.key === 'Home') {
    emit('update:value', props.invert ? props.max : props.min)
  } else if (event.key === 'End') {
    emit('update:value', props.invert ? props.min : props.max)
  } else if (event.key === 'Enter') {
    emit('reset')
  } else {
    return
  }
  event.preventDefault()
}

// A drag interrupted by the component unmounting would otherwise leave the
// document unselectable with a resize cursor.
onBeforeUnmount(() => setDocumentDragState(false))
</script>

<template>
  <button
    type="button"
    class="s-resize-handle"
    :class="{ 's-resize-handle--dragging': dragging }"
    role="separator"
    aria-orientation="vertical"
    :aria-label="label"
    :aria-valuenow="value"
    :aria-valuemin="min"
    :aria-valuemax="max"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="endDrag"
    @pointercancel="endDrag"
    @lostpointercapture="endDrag"
    @keydown="onKeydown"
  />
</template>

<style scoped>
.s-resize-handle {
  position: relative;
  width: 4px;
  flex-shrink: 0;
  appearance: none;
  border: none;
  padding: 0;
  cursor: col-resize;
  background: var(--color-border);
  touch-action: none;
}

/* The visible seam stays 4px while the pointer/touch target reaches the 44px
   floor (R24.34). Negative insets rather than padding so the hit area does not
   displace the grid track. */
.s-resize-handle::before {
  content: '';
  position: absolute;
  inset: 0 -20px;
}

.s-resize-handle:hover,
.s-resize-handle--dragging {
  background: var(--color-accent);
}

.s-resize-handle:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

@media (prefers-reduced-motion: no-preference) {
  .s-resize-handle {
    transition: background var(--transition-fast);
  }
}
</style>
