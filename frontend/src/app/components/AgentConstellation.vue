<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

// Decorative hero visual: a central orchestrator hub linked to a ring of
// heterogeneous agent nodes, with comet-tailed pulses flowing both inward
// (agents reporting) and outward (the hub dispatching) to evoke multi-agent
// orchestration. Pure inline SVG + CSS keyframes — no runtime deps. The global
// prefers-reduced-motion rule (shared/styles/main.css) freezes every animation,
// leaving a clean static topology, so motion is never required to read it.

const center = { x: 200, y: 200 }
const radius = 150

// Heterogeneous nodes tell the "multi-LLM, mixed providers" story: varied
// radii, a couple highlighted as primaries, alternating flow direction.
const NODES = [
  { deg: -90, r: 12, primary: true },
  { deg: -30, r: 8, primary: false },
  { deg: 30, r: 10, primary: false },
  { deg: 90, r: 9, primary: true },
  { deg: 150, r: 8, primary: false },
  { deg: 210, r: 11, primary: false },
]

const satellites = NODES.map((n, i) => {
  const rad = (n.deg * Math.PI) / 180
  return {
    id: i,
    x: Math.round(center.x + radius * Math.cos(rad)),
    y: Math.round(center.y + radius * Math.sin(rad)),
    r: n.r,
    primary: n.primary,
    inward: i % 2 === 0,
  }
})

// Pointer parallax — a restrained ±6deg tilt that follows the cursor. Opt-in
// only on fine pointers with motion enabled; touch and reduced-motion users get
// a flat, static figure.
const root = ref<SVGSVGElement | null>(null)
const tiltX = ref(0)
const tiltY = ref(0)
const MAX_TILT = 6
let detach: (() => void) | null = null

function onMove(e: PointerEvent): void {
  const el = root.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const px = (e.clientX - rect.left) / rect.width - 0.5
  const py = (e.clientY - rect.top) / rect.height - 0.5
  tiltY.value = px * MAX_TILT * 2
  tiltX.value = -py * MAX_TILT * 2
}

function onLeave(): void {
  tiltX.value = 0
  tiltY.value = 0
}

onMounted(() => {
  const el = root.value
  if (!el || typeof window.matchMedia !== 'function') return
  const noMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const coarse = !window.matchMedia('(pointer: fine)').matches
  if (noMotion || coarse) return
  el.addEventListener('pointermove', onMove)
  el.addEventListener('pointerleave', onLeave)
  detach = () => {
    el.removeEventListener('pointermove', onMove)
    el.removeEventListener('pointerleave', onLeave)
  }
})

onBeforeUnmount(() => detach?.())
</script>

<template>
  <svg
    ref="root"
    class="constellation"
    viewBox="0 0 400 400"
    role="presentation"
    aria-hidden="true"
    focusable="false"
    :style="{
      transform: `perspective(900px) rotateX(${tiltX}deg) rotateY(${tiltY}deg)`,
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
    </defs>

    <circle
      :cx="center.x"
      :cy="center.y"
      r="150"
      fill="url(#ac-hub-glow)"
    />

    <!-- Slowly rotating decorative orbit; nodes stay put so nothing readable
         drifts. -->
    <g class="orbit">
      <circle
        class="orbit-ring"
        :cx="center.x"
        :cy="center.y"
        r="184"
      />
    </g>

    <!-- Edges: a faint static rail plus a comet-tailed pulse. Even edges flow
         outward, odd edges inward, for request/response variety. -->
    <g class="edges">
      <template
        v-for="node in satellites"
        :key="`edge-${node.id}`"
      >
        <line
          class="edge-rail"
          :x1="center.x"
          :y1="center.y"
          :x2="node.x"
          :y2="node.y"
        />
        <line
          class="edge-flow"
          :class="{ 'edge-flow--inward': node.inward }"
          :x1="center.x"
          :y1="center.y"
          :x2="node.x"
          :y2="node.y"
          :style="{ animationDelay: `${node.id * 0.42}s` }"
        />
      </template>
    </g>

    <!-- Satellite agent nodes. -->
    <g class="nodes">
      <circle
        v-for="node in satellites"
        :key="`node-${node.id}`"
        class="node"
        :class="{ 'node--primary': node.primary }"
        :cx="node.x"
        :cy="node.y"
        :r="node.r"
        :style="{ animationDelay: `${node.id * 0.35}s` }"
      />
    </g>

    <!-- Central orchestrator hub with an expanding "active" ping. -->
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
      class="hub"
      :cx="center.x"
      :cy="center.y"
      r="15"
    />
  </svg>
</template>

<style scoped>
.constellation {
  width: 100%;
  height: auto;
  max-width: 460px;
  overflow: visible;
  will-change: transform;
  transition: transform 140ms ease-out;
}

.orbit {
  transform-box: fill-box;
  transform-origin: center;
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
  stroke: var(--color-border);
  stroke-width: 1.5;
}

.edge-flow {
  /* All six edges span exactly the ring radius (150 user units); the dash
     pattern sums to 150 so the comet loops seamlessly. The tapered segments
     (16 / 6 / 3) read as a head with a fading tail. */
  stroke: var(--color-accent);
  stroke-width: 2.5;
  stroke-linecap: round;
  stroke-dasharray: 16 8 6 12 3 105;
  filter: drop-shadow(0 0 3px var(--color-accent));
  animation: ac-flow 2.8s linear infinite;
}

.edge-flow--inward {
  animation-direction: reverse;
}

@keyframes ac-flow {
  from {
    stroke-dashoffset: 150;
  }
  to {
    stroke-dashoffset: 0;
  }
}

.node {
  fill: var(--color-bg);
  stroke: var(--color-accent);
  stroke-width: 2;
  transform-box: fill-box;
  transform-origin: center;
  animation: ac-breathe 4s ease-in-out infinite;
}

.node--primary {
  fill: var(--color-accent);
  stroke: none;
}

@keyframes ac-breathe {
  0%,
  100% {
    transform: scale(1);
    opacity: 0.92;
  }
  50% {
    transform: scale(1.12);
    opacity: 1;
  }
}

.hub-ping {
  fill: none;
  stroke: var(--color-accent);
  stroke-width: 1.5;
  transform-box: fill-box;
  transform-origin: center;
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

.hub {
  fill: var(--color-accent);
  transform-box: fill-box;
  transform-origin: center;
  animation: ac-hub-pulse 2.8s ease-in-out infinite;
}

@keyframes ac-hub-pulse {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.08);
  }
}
</style>
