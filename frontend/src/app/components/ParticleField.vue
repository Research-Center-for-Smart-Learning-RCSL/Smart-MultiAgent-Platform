<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { usePrefersReducedMotion } from '@shared/composables'
import { createParticleField, type ParticleFieldHandle } from './particleField'

// Ambient particle field rendered behind the landing constellation and the
// entry overlay. Thin lifecycle adapter over particleField.ts: it wires up
// sizing (DPR-aware), viewport pausing, live theme re-reads, and pointer
// repulsion, and stays fully inert (blank canvas, no rAF loop) under
// prefers-reduced-motion. Purely decorative — pointer-events: none and hidden
// from assistive tech.

const props = withDefaults(defineProps<{ count?: number }>(), { count: 48 })

const canvasEl = ref<HTMLCanvasElement | null>(null)
const reduced = usePrefersReducedMotion()

let field: ParticleFieldHandle | null = null
let inView = true
let visibility: IntersectionObserver | null = null
let sizeObserver: ResizeObserver | null = null
let themeObserver: MutationObserver | null = null
let pointerHost: HTMLElement | null = null
let canvasRect: DOMRect | null = null

function syncRunning(): void {
  if (!field) return
  if (inView && !reduced.value) field.start()
  else field.stop()
}

// The canvas itself is pointer-events: none (it sits behind content), so the
// repulsion listens on the parent container. Fine pointers only — touch gets a
// calm, undisturbed field.
function onPointerMove(e: PointerEvent): void {
  if (!field || !canvasRect) return
  field.setPointer(e.clientX - canvasRect.left, e.clientY - canvasRect.top)
}

function onPointerEnter(): void {
  canvasRect = canvasEl.value?.getBoundingClientRect() ?? null
}

function onPointerLeave(): void {
  canvasRect = null
  field?.clearPointer()
}

onMounted(() => {
  const canvas = canvasEl.value
  if (!canvas) return
  // jsdom (tests) and exotic embeds have no 2D context — degrade to a blank,
  // harmless canvas.
  let ctx: CanvasRenderingContext2D | null = null
  try {
    ctx = canvas.getContext('2d')
  } catch {
    ctx = null
  }
  if (!ctx) return

  field = createParticleField(canvas, ctx, { count: props.count })
  field.resize()
  field.readTheme()

  if (typeof ResizeObserver !== 'undefined') {
    sizeObserver = new ResizeObserver(() => field?.resize())
    sizeObserver.observe(canvas)
  }

  if (typeof IntersectionObserver !== 'undefined') {
    visibility = new IntersectionObserver(
      (entries) => {
        inView = entries.some((e) => e.isIntersecting)
        syncRunning()
      },
      { threshold: 0 },
    )
    visibility.observe(canvas)
  }

  // Accent token flips with the theme attribute; re-read instead of polling.
  if (typeof MutationObserver !== 'undefined') {
    themeObserver = new MutationObserver(() => field?.readTheme())
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    })
  }

  if (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(pointer: fine)').matches
  ) {
    pointerHost = canvas.parentElement
    if (pointerHost) {
      pointerHost.addEventListener('pointerenter', onPointerEnter)
      pointerHost.addEventListener('pointermove', onPointerMove)
      pointerHost.addEventListener('pointerleave', onPointerLeave)
    }
  }

  syncRunning()
  watch(reduced, syncRunning)
})

onBeforeUnmount(() => {
  if (pointerHost) {
    pointerHost.removeEventListener('pointerenter', onPointerEnter)
    pointerHost.removeEventListener('pointermove', onPointerMove)
    pointerHost.removeEventListener('pointerleave', onPointerLeave)
    pointerHost = null
  }
  sizeObserver?.disconnect()
  visibility?.disconnect()
  themeObserver?.disconnect()
  field?.destroy()
  field = null
})
</script>

<template>
  <canvas
    ref="canvasEl"
    class="particle-field"
    aria-hidden="true"
  />
</template>

<style scoped>
.particle-field {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
</style>
