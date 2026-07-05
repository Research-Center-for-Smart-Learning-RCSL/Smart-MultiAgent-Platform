<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { usePrefersReducedMotion } from '@shared/composables'
import {
  CENTER as center,
  RADIUS as radius,
  SATELLITES,
  BALL_COUNT,
  edgePath,
} from './constellation'
import ParticleField from './ParticleField.vue'

// Decorative hero visual: a central orchestrator hub linked to a ring of
// heterogeneous agent nodes, with comet-tailed pulses flowing both inward
// (agents reporting) and outward (the hub dispatching) along gently bowed
// curves to evoke multi-agent orchestration. Every ball (hub + 6 satellites)
// breathes through an empty -> filling -> released cycle staggered around the
// ring, cores carry an SVG bloom so ignition reads as light, and outward
// satellites answer each arriving pulse with a small elastic "receive" bump.
// Three depth layers (ambient particle field, orbit chrome, node figure) shift
// at different rates under the pointer tilt so the parallax reads as real
// depth. Pure inline SVG + CSS keyframes plus one canvas layer — no runtime
// deps. The global prefers-reduced-motion rule (shared/styles/main.css)
// collapses each animation to its final keyframe (a fully-filled core), and the
// particle layer disables itself, leaving a clean static topology.

// Decorative ring sits just outside the node circle.
const orbitRadius = radius + 34

// Drives both the phase stagger below and the CSS animation duration (bound as
// --fill-cycle on the root), so the JS timing and the stylesheet cannot drift.
const FILL_CYCLE_S = 4
const fillDelay = (phase: number): string => `${-(phase / BALL_COUNT) * FILL_CYCLE_S}s`

// Comet pulse cycle (also the receive-pulse cycle, so they stay in step).
const FLOW_CYCLE_S = 2.8
// The pulse head reaches the satellite late in the cycle; the receive bump is
// timed to that arrival. Empirical fraction of the dash travel.
const RECEIVE_AT = 0.8

const satellites = SATELLITES.map((n) => ({
  ...n,
  path: edgePath(n),
  // Satellites occupy fill phases 1..6; the hub holds phase 0.
  fillDelay: fillDelay(n.id + 1),
  // Odd edges flow inward (agents reporting), even edges outward (hub
  // dispatching), for request/response variety.
  inward: n.id % 2 === 1,
  flowDelay: n.id * 0.42,
  receiveDelay: `${(n.id * 0.42 + FLOW_CYCLE_S * RECEIVE_AT).toFixed(2)}s`,
}))

const root = ref<HTMLElement | null>(null)
const reduced = usePrefersReducedMotion()

// Pause the perpetual keyframe animations whenever the figure leaves the
// viewport — it is purely decorative, so spending frames on it offscreen is
// wasted work. (The particle layer runs its own identical observer.)
const paused = ref(false)
let visibility: IntersectionObserver | null = null

// Pointer parallax — a restrained ±6deg tilt that follows the cursor, plus a
// normalized offset (--par-x/--par-y in [-1, 1]) that the depth layers scale
// by their own factor. Opt-in only on fine pointers with motion enabled; touch
// and reduced-motion users get a flat, static figure. Moves are coalesced to
// one write per frame, and the bounding rect is cached on enter so no move
// forces a layout reflow.
const tiltX = ref(0)
const tiltY = ref(0)
const parX = ref(0)
const parY = ref(0)
const MAX_TILT = 6
let rect: DOMRect | null = null
let lastEvent: PointerEvent | null = null
let rafId = 0
let pointerAttached = false

function applyTilt(): void {
  rafId = 0
  if (!lastEvent || !rect) return
  const px = (lastEvent.clientX - rect.left) / rect.width - 0.5
  const py = (lastEvent.clientY - rect.top) / rect.height - 0.5
  tiltY.value = px * MAX_TILT * 2
  tiltX.value = -py * MAX_TILT * 2
  parX.value = Math.max(-1, Math.min(1, px * 2))
  parY.value = Math.max(-1, Math.min(1, py * 2))
}

function onEnter(): void {
  rect = root.value?.getBoundingClientRect() ?? null
}

function onMove(e: PointerEvent): void {
  lastEvent = e
  if (!rafId) rafId = requestAnimationFrame(applyTilt)
}

function onLeave(): void {
  if (rafId) {
    cancelAnimationFrame(rafId)
    rafId = 0
  }
  lastEvent = null
  rect = null
  tiltX.value = 0
  tiltY.value = 0
  parX.value = 0
  parY.value = 0
}

function attachPointer(): void {
  const el = root.value
  if (pointerAttached || !el) return
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
  if (!window.matchMedia('(pointer: fine)').matches) return
  pointerAttached = true
  el.addEventListener('pointerenter', onEnter)
  el.addEventListener('pointermove', onMove)
  el.addEventListener('pointerleave', onLeave)
}

function detachPointer(): void {
  const el = root.value
  if (!pointerAttached || !el) return
  pointerAttached = false
  el.removeEventListener('pointerenter', onEnter)
  el.removeEventListener('pointermove', onMove)
  el.removeEventListener('pointerleave', onLeave)
  onLeave()
}

onMounted(() => {
  const el = root.value
  if (!el) return

  if (!reduced.value) attachPointer()
  // React live to the OS reduced-motion setting being toggled.
  watch(reduced, (r) => (r ? detachPointer() : attachPointer()))

  if (typeof IntersectionObserver !== 'undefined') {
    visibility = new IntersectionObserver(
      (entries) => {
        paused.value = !entries.some((e) => e.isIntersecting)
      },
      { threshold: 0 },
    )
    visibility.observe(el)
  }
})

onBeforeUnmount(() => {
  detachPointer()
  visibility?.disconnect()
  visibility = null
})
</script>

<template>
  <div
    ref="root"
    class="constellation-wrap"
    :class="{ 'is-paused': paused }"
    role="presentation"
    aria-hidden="true"
    :style="{ '--par-x': parX, '--par-y': parY }"
  >
    <ParticleField
      class="constellation-particles"
      :count="42"
    />
    <svg
      class="constellation"
      viewBox="0 0 400 400"
      focusable="false"
      :style="{
        transform: `perspective(900px) rotateX(${tiltX}deg) rotateY(${tiltY}deg)`,
        '--fill-cycle': `${FILL_CYCLE_S}s`,
      }"
    >
      <defs>
        <radialGradient
          id="ac-hub-glow"
          cx="50%"
          cy="50%"
          r="50%"
        >
          <stop
            offset="0%"
            stop-color="var(--color-accent)"
            stop-opacity="0.3"
          />
          <stop
            offset="100%"
            stop-color="var(--color-accent)"
            stop-opacity="0"
          />
        </radialGradient>

        <!-- Soft bloom behind each lit core: blurred copy merged under the
             crisp source. The region is kept tight so low-end GPUs are not
             asked to filter a large area. -->
        <filter
          id="ac-bloom"
          x="-75%"
          y="-75%"
          width="250%"
          height="250%"
        >
          <feGaussianBlur
            in="SourceGraphic"
            stdDeviation="3.5"
            result="blur"
          />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>

        <!-- One gradient per edge, hub end in accent, satellite end in
             accent-2, in user space so the stroke direction follows the
             spoke. -->
        <linearGradient
          v-for="node in satellites"
          :id="`ac-edge-grad-${node.id}`"
          :key="`grad-${node.id}`"
          gradientUnits="userSpaceOnUse"
          :x1="center.x"
          :y1="center.y"
          :x2="node.x"
          :y2="node.y"
        >
          <stop
            offset="0%"
            stop-color="var(--color-accent)"
          />
          <stop
            offset="100%"
            stop-color="var(--color-accent-2)"
          />
        </linearGradient>
      </defs>

      <!-- Mid depth layer: hub glow wash and the slowly rotating orbit ring.
           Nodes stay put so nothing readable drifts. -->
      <g class="layer-mid">
        <circle
          :cx="center.x"
          :cy="center.y"
          :r="radius"
          fill="url(#ac-hub-glow)"
        />
        <g class="orbit">
          <circle
            class="orbit-ring"
            :cx="center.x"
            :cy="center.y"
            :r="orbitRadius"
          />
        </g>
      </g>

      <!-- Foreground depth layer: the figure itself. Edges and nodes shift
           together so the curves never detach from their endpoints. -->
      <g class="layer-fg">
        <!-- Edges: a faint static rail plus a comet-tailed pulse, alternating
             inward/outward (see `inward` above). pathLength normalizes every
             curve to 100 units so one dash pattern serves all six. -->
        <g class="edges">
          <template
            v-for="node in satellites"
            :key="`edge-${node.id}`"
          >
            <path
              class="edge-rail"
              :d="node.path"
            />
            <path
              class="edge-flow"
              :class="{ 'edge-flow--inward': node.inward }"
              :d="node.path"
              pathLength="100"
              :stroke="`url(#ac-edge-grad-${node.id})`"
              :style="{ animationDelay: `${node.flowDelay}s` }"
            />
          </template>
        </g>

        <!-- Satellite agent nodes: a hollow shell with an accent core that
             grows to fill it then contracts back out. Outward (dispatch)
             targets bump elastically when the comet pulse arrives. -->
        <g class="nodes">
          <template
            v-for="node in satellites"
            :key="`node-${node.id}`"
          >
            <circle
              class="node-shell"
              :class="{ 'node-shell--receive': !node.inward }"
              :cx="node.x"
              :cy="node.y"
              :r="node.r"
              :style="!node.inward ? { animationDelay: node.receiveDelay } : undefined"
            />
            <circle
              class="node-fill"
              filter="url(#ac-bloom)"
              :cx="node.x"
              :cy="node.y"
              :r="node.r"
              :style="{ animationDelay: node.fillDelay }"
            />
          </template>
        </g>

        <!-- Central orchestrator hub with an expanding "active" ping. Shares
             the fill cycle as phase 0, anchoring the wave that travels out to
             the ring. -->
        <circle
          class="hub-ping"
          :cx="center.x"
          :cy="center.y"
          r="18"
        />
        <circle
          class="hub-ring"
          :cx="center.x"
          :cy="center.y"
          r="24"
        />
        <circle
          class="hub-shell"
          :cx="center.x"
          :cy="center.y"
          r="15"
        />
        <circle
          class="hub-fill"
          filter="url(#ac-bloom)"
          :cx="center.x"
          :cy="center.y"
          r="15"
        />
      </g>
    </svg>
  </div>
</template>

<style scoped>
.constellation-wrap {
  position: relative;
  width: 100%;
  max-width: 460px;
}

/* Deepest parallax layer: drifts opposite the pointer at the smallest rate,
   and bleeds past the figure so the ambience is not a visible box. Explicit
   box (not inset) because a canvas is a replaced element whose 100% size wins
   over opposing offsets; the descendant selector outranks the component's own
   base rule regardless of style injection order. */
.constellation-wrap > .constellation-particles {
  top: -48px;
  left: -48px;
  width: calc(100% + 96px);
  height: calc(100% + 96px);
  transform: translate3d(calc(var(--par-x, 0) * -8px), calc(var(--par-y, 0) * -8px), 0);
  transition: transform 200ms ease-out;
}

.constellation {
  position: relative;
  display: block;
  width: 100%;
  height: auto;
  overflow: visible;
  will-change: transform;
  transition: transform 140ms ease-out;
}

/* Depth layers inside the figure: chrome trails the pointer, the figure leads
   it, so the tilt reads as volume instead of a flat card. */
.layer-mid {
  transform: translate3d(calc(var(--par-x, 0) * -4px), calc(var(--par-y, 0) * -4px), 0);
  transition: transform 160ms ease-out;
}

.layer-fg {
  transform: translate3d(calc(var(--par-x, 0) * 5px), calc(var(--par-y, 0) * 5px), 0);
  transition: transform 140ms ease-out;
}

/* SVG transforms must originate from each element's own box centre, not the
   shared viewport origin. */
.orbit,
.node-shell,
.node-fill,
.hub-ping,
.hub-fill {
  transform-box: fill-box;
  transform-origin: center;
}

/* Freeze every animation while the figure is scrolled out of view. */
.constellation-wrap.is-paused .orbit,
.constellation-wrap.is-paused .edge-flow,
.constellation-wrap.is-paused .node-shell,
.constellation-wrap.is-paused .node-fill,
.constellation-wrap.is-paused .hub-ping,
.constellation-wrap.is-paused .hub-fill {
  animation-play-state: paused;
}

.orbit {
  animation: ac-orbit 50s linear infinite;
}

.orbit-ring {
  fill: none;
  stroke: var(--color-border);
  stroke-width: 1;
  stroke-dasharray: 2 10;
  opacity: 0.6;
}

@keyframes ac-orbit {
  to {
    transform: rotate(360deg);
  }
}

.edge-rail {
  fill: none;
  stroke: var(--color-border);
  stroke-width: 1.5;
}

.edge-flow {
  /* pathLength=100 normalizes every curve, so the dash pattern sums to 100 and
     the comet loops seamlessly on all six edges. The tapered segments
     (10.5 / 4 / 2) read as a head with a fading tail. */
  fill: none;
  stroke-width: 2.5;
  stroke-linecap: round;
  stroke-dasharray: 10.5 5.5 4 8 2 70;
  filter: drop-shadow(0 0 3px var(--color-accent));
  animation: ac-flow 2.8s linear infinite;
}

.edge-flow--inward {
  animation-direction: reverse;
}

@keyframes ac-flow {
  from {
    stroke-dashoffset: 100;
  }
  to {
    stroke-dashoffset: 0;
  }
}

.node-shell,
.hub-shell {
  fill: var(--color-bg);
  stroke: var(--color-accent);
  stroke-width: 2;
}

/* Dispatch targets answer the arriving comet with a quick elastic bump. The
   cycle mirrors ac-flow (2.8s) and the inline delay lands it at pulse
   arrival; the resting keyframes are scale(1) so reduced-motion and pauses
   settle clean. */
.node-shell--receive {
  animation: ac-receive 2.8s ease-out infinite;
}

@keyframes ac-receive {
  0%,
  14%,
  100% {
    transform: scale(1);
  }
  6% {
    transform: scale(1.14);
  }
}

/* The hub holds phase 0 (no inline delay); satellites are offset around it. */
.node-fill,
.hub-fill {
  fill: var(--color-accent);
  animation: ac-fill var(--fill-cycle) ease-in-out infinite;
}

/* The accent core empties (scale 0) at mid-cycle and rests filled (scale 1) at
   the ends — so reduced-motion, which snaps to the final keyframe, settles on a
   complete, fully-filled glyph. */
@keyframes ac-fill {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(0);
  }
}

.hub-ping {
  fill: none;
  stroke: var(--color-accent);
  stroke-width: 1.5;
  animation: ac-ping 2.8s ease-out infinite;
}

@keyframes ac-ping {
  0% {
    transform: scale(1);
    opacity: 0.5;
  }
  70%,
  100% {
    transform: scale(2.1);
    opacity: 0;
  }
}

.hub-ring {
  fill: none;
  stroke: var(--color-accent);
  stroke-width: 1.5;
  opacity: 0.35;
}
</style>
