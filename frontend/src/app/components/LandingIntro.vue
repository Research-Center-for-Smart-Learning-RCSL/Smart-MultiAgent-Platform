<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import BrandLogo from './BrandLogo.vue'
import ParticleField from './ParticleField.vue'
import { CENTER as center, SATELLITES, edgePath } from './constellation'

// Full-screen brand intro played every time the landing route mounts — a
// ~2.7s four-act sequence:
//   Act 1 (0-0.5s)    quiet open: the hub surfaces alone while the accent wash
//                     and ambient particle field fade up around it.
//   Act 2 (0.5-1.4s)  convergence: the six satellites fly home along slight
//                     arcs, each arrival marked by a brief bloom flash.
//   Act 3 (1.4-2.0s)  ignition: gradient links draw along the shared curves,
//                     the cores light in a sweep, and the hub ignites with a
//                     double shockwave — the climax beat.
//   Act 4 (2.0-2.7s)  signature: the wordmark settles out of wide tracking,
//                     holds, then the assembled glyph docks into the hero
//                     constellation's slot (measured at runtime) while the
//                     backdrop clears, reading as one continuous motion.
// User input instead fast-forwards to a quick fade. The geometry (positions
// and edge curves) mirrors AgentConstellation so the docked glyph and the
// hero are pixel-identical at the swap.
//
// The caller only mounts this when motion is allowed, so there is no
// reduced-motion path here beyond the global stylesheet freeze.

const props = defineProps<{ target?: HTMLElement | null }>()
// `reveal` tells the parent to show the hero; `done` tells it to unmount this
// overlay. On a dock they fire together (seamless swap); on a plain fade `reveal`
// fires first so the hero is visible beneath the fading curtain.
const emit = defineEmits<{ reveal: []; done: [] }>()

const { t } = useI18n()

// Distance each ball travels inward along its own radius as it converges, and
// the perpendicular bow of the flight arc (alternating sides, like the edges).
const CONVERGE = 150
const ARC = 30

// Act anchors (seconds) for the per-node beats below.
const ACT2_AT = 0.45
const ACT3_AT = 1.35
const HUB_IGNITE_AT = 1.75

const nodes = SATELLITES.map((n) => {
  const side = n.id % 2 === 0 ? 1 : -1
  const inDelay = ACT2_AT + n.id * 0.11
  return {
    ...n,
    path: edgePath(n),
    // Start pushed outward along the radial, then slide home.
    tx: Math.round(n.cos * CONVERGE),
    ty: Math.round(n.sin * CONVERGE),
    // Mid-flight perpendicular offset that bends the approach into an arc.
    ax: Math.round(-n.sin * ARC * side),
    ay: Math.round(n.cos * ARC * side),
    inDelay,
    // Arrival flash fires as the fly-in settles.
    flashDelay: inDelay + 0.55,
    drawDelay: ACT3_AT + n.id * 0.07,
    fillDelay: 1.5 + n.id * 0.06,
  }
})

// Timeline anchors (ms). The body plays through the four acts, then either
// docks into the hero or, on skip/no-target, fades out. DOCK_MS and FADE_MS
// are bound to the CSS exit transitions via custom properties (below), so each
// duration has a single source of truth shared by the timer and the stylesheet.
const BODY_MS = 2700
const DOCK_MS = 560
const FADE_MS = 300
// Skip hint appears partway in, so a returning visitor has an obvious exit.
const HINT_MS = 900

const svgEl = ref<SVGSVGElement | null>(null)
const leaving = ref(false)
const docking = ref(false)
const skipHintOn = ref(false)
const dockStyle = ref<Record<string, string>>({})

let bodyTimer: ReturnType<typeof setTimeout> | null = null
let liftTimer: ReturnType<typeof setTimeout> | null = null
let hintTimer: ReturnType<typeof setTimeout> | null = null
let finished = false

const SKIP_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const

// Map the assembled glyph onto the hero constellation's box (centre-to-centre
// translate + size-ratio scale). Returns null when either box is unmeasurable so
// the caller can fall back to a plain fade.
function measureDock(): Record<string, string> | null {
  const svg = svgEl.value
  const targetSvg = props.target?.querySelector('.constellation')
  if (!svg || !targetSvg) return null
  const src = svg.getBoundingClientRect()
  const tgt = targetSvg.getBoundingClientRect()
  if (!src.width || !tgt.width) return null
  const scale = tgt.width / src.width
  const dx = tgt.left + tgt.width / 2 - (src.left + src.width / 2)
  const dy = tgt.top + tgt.height / 2 - (src.top + src.height / 2)
  return { transform: `translate(${Math.round(dx)}px, ${Math.round(dy)}px) scale(${scale.toFixed(4)})` }
}

function lift(dock: boolean): void {
  if (leaving.value) return
  leaving.value = true
  skipHintOn.value = false
  if (bodyTimer) clearTimeout(bodyTimer)
  if (hintTimer) clearTimeout(hintTimer)

  const style = dock ? measureDock() : null
  if (style) {
    docking.value = true
    dockStyle.value = style
    liftTimer = setTimeout(() => {
      emit('reveal')
      finish()
    }, DOCK_MS)
  } else {
    // Plain fade: reveal the hero now so it shows through the fading curtain.
    emit('reveal')
    liftTimer = setTimeout(finish, FADE_MS)
  }
}

function finish(): void {
  if (finished) return
  finished = true
  emit('done')
}

function skip(): void {
  lift(false)
}

onMounted(() => {
  bodyTimer = setTimeout(() => lift(true), BODY_MS)
  hintTimer = setTimeout(() => {
    if (!leaving.value) skipHintOn.value = true
  }, HINT_MS)
  for (const name of SKIP_EVENTS) {
    window.addEventListener(name, skip, { passive: true })
  }
})

onBeforeUnmount(() => {
  if (bodyTimer) clearTimeout(bodyTimer)
  if (liftTimer) clearTimeout(liftTimer)
  if (hintTimer) clearTimeout(hintTimer)
  for (const name of SKIP_EVENTS) window.removeEventListener(name, skip)
})
</script>

<template>
  <div
    class="intro"
    :class="{ 'intro--leaving': leaving, 'intro--docking': docking }"
    :style="{ '--dock-dur': `${DOCK_MS}ms`, '--fade-dur': `${FADE_MS}ms` }"
    role="presentation"
    aria-hidden="true"
  >
    <div class="intro__wash" />
    <ParticleField
      class="intro__particles"
      :count="36"
    />
    <div class="intro__stage">
      <svg
        ref="svgEl"
        class="intro__svg"
        viewBox="0 0 400 400"
        focusable="false"
        :style="dockStyle"
      >
        <defs>
          <!-- Same bloom treatment as the hero, so ignition reads as light. -->
          <filter
            id="ai-bloom"
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

          <linearGradient
            v-for="node in nodes"
            :id="`ai-edge-grad-${node.id}`"
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

        <!-- Act 3: gradient links drawn from the hub outward along the same
             curves the hero uses. -->
        <g class="intro__edges">
          <path
            v-for="node in nodes"
            :key="`edge-${node.id}`"
            class="intro-edge"
            :d="node.path"
            pathLength="100"
            :stroke="`url(#ai-edge-grad-${node.id})`"
            :style="{ animationDelay: `${node.drawDelay}s` }"
          />
        </g>

        <!-- Act 2: satellite balls arc home; an arrival flash marks each
             landing. Act 3 then ignites their cores in a sweep. -->
        <g
          v-for="node in nodes"
          :key="`node-${node.id}`"
          class="intro-node"
          :style="{
            '--tx': `${node.tx}px`,
            '--ty': `${node.ty}px`,
            '--ax': `${node.ax}px`,
            '--ay': `${node.ay}px`,
            animationDelay: `${node.inDelay}s`,
          }"
        >
          <circle
            class="intro-node__flash"
            filter="url(#ai-bloom)"
            :cx="node.x"
            :cy="node.y"
            :r="node.r + 3"
            :style="{ animationDelay: `${node.flashDelay}s` }"
          />
          <circle
            class="intro-node__shell"
            :cx="node.x"
            :cy="node.y"
            :r="node.r"
          />
          <circle
            class="intro-node__fill"
            filter="url(#ai-bloom)"
            :cx="node.x"
            :cy="node.y"
            :r="node.r"
            :style="{ animationDelay: `${node.fillDelay}s` }"
          />
        </g>

        <!-- Act 3 climax: the hub ignition throws a double shockwave. -->
        <circle
          class="intro-shock intro-shock--a"
          :cx="center.x"
          :cy="center.y"
          r="18"
          :style="{ animationDelay: `${HUB_IGNITE_AT + 0.03}s` }"
        />
        <circle
          class="intro-shock intro-shock--b"
          :cx="center.x"
          :cy="center.y"
          r="18"
          :style="{ animationDelay: `${HUB_IGNITE_AT + 0.16}s` }"
        />

        <!-- Act 1: the hub surfaces first, alone; its core ignites at the
             act-3 climax. -->
        <g class="intro-node intro-hub">
          <circle
            class="intro-node__shell"
            :cx="center.x"
            :cy="center.y"
            r="15"
          />
          <circle
            class="intro-node__fill"
            filter="url(#ai-bloom)"
            :cx="center.x"
            :cy="center.y"
            r="15"
            :style="{ animationDelay: `${HUB_IGNITE_AT}s` }"
          />
        </g>
      </svg>

      <BrandLogo
        size="lg"
        class="intro__word"
      />
    </div>

    <span
      class="intro__skip"
      :class="{ 'intro__skip--on': skipHintOn }"
    >
      {{ t('app.landing.introSkip') }}
    </span>
  </div>
</template>

<style scoped>
.intro {
  position: fixed;
  inset: 0;
  /* Above every in-app layer (tooltip token tops out at 600) so the curtain
     covers the whole viewport during entry. */
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--color-bg);
  opacity: 1;
  transform: scale(1);
  transition:
    opacity var(--fade-dur) ease,
    transform var(--fade-dur) ease,
    background-color 0.45s ease;
}

/* Skip / no-target exit: fade the whole curtain out. */
.intro--leaving:not(.intro--docking) {
  opacity: 0;
  transform: scale(1.03);
  pointer-events: none;
}

/* Dock exit: the glyph flies to the hero slot, so the backdrop and chrome clear
   to expose the page beneath while the svg itself transforms. */
.intro--docking {
  background-color: transparent;
  pointer-events: none;
}

.intro--docking .intro__wash,
.intro--docking .intro__particles,
.intro--docking .intro__word {
  opacity: 0;
  transition: opacity 0.4s ease;
}

/* Soft accent wash echoing the hero background, so the reveal feels continuous. */
.intro__wash {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(
    60% 60% at 50% 42%,
    color-mix(in srgb, var(--color-accent) 14%, transparent),
    transparent 70%
  );
  opacity: 0.7;
  /* `backwards` (not `both`): reverts to the base value once done, so the
     dock-exit opacity transition above is not blocked by a held animation
     value. Same reasoning for the particle and wordmark entrances below. */
  animation: intro-wash 1.3s ease-out backwards;
}

/* Act 1: ambient depth fades up with the wash. */
.intro__particles {
  animation: intro-particles-in 1.2s ease-out backwards;
}

.intro__stage {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 28px;
}

.intro__svg {
  width: min(64vmin, 520px);
  height: auto;
  overflow: visible;
  transform-box: border-box;
  transform-origin: center;
  transition: transform var(--dock-dur) cubic-bezier(0.5, 0, 0.2, 1);
}

/* SVG transforms originate from each element's own box centre. */
.intro-node,
.intro-node__fill,
.intro-node__flash,
.intro-shock {
  transform-box: fill-box;
  transform-origin: center;
}

.intro-node {
  animation: intro-node-in 0.7s cubic-bezier(0.3, 0.9, 0.35, 1) both;
}

/* The hub belongs to act 1: it surfaces immediately, before the satellites. */
.intro-hub {
  animation: intro-hub-in 0.6s ease-out 0.05s both;
}

.intro-node__shell {
  fill: var(--color-bg);
  stroke: var(--color-accent);
  stroke-width: 2;
}

.intro-node__fill {
  fill: var(--color-accent);
  /* One-shot ignite — settles filled so the docked glyph is complete. */
  animation: intro-fill 0.5s ease-out both;
}

/* Arrival flash: a bloomed accent halo that blinks once as the ball lands. */
.intro-node__flash {
  fill: var(--color-accent);
  animation: intro-flash 0.45s ease-out both;
}

.intro-edge {
  fill: none;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-dasharray: 100;
  filter: drop-shadow(0 0 3px var(--color-accent));
  animation: intro-edge-draw 0.45s ease-out both;
}

/* Climax shockwaves: two expanding rings, the second wider and fainter. */
.intro-shock {
  fill: none;
  stroke: var(--color-accent);
  stroke-width: 1.5;
}

.intro-shock--a {
  animation: intro-shock-a 0.85s ease-out both;
}

.intro-shock--b {
  animation: intro-shock-b 1s ease-out both;
}

.intro__word {
  /* Act 4: the wordmark settles out of wide letter tracking. */
  animation: intro-word-in 0.6s cubic-bezier(0.2, 0.7, 0.3, 1) 2.05s backwards;
}

/* Skip affordance: driven by a transition (not a keyframe) so it can fade both
   in and back out cleanly when the curtain leaves. */
.intro__skip {
  position: absolute;
  bottom: 7%;
  left: 50%;
  transform: translateX(-50%);
  font-size: 0.8125rem;
  letter-spacing: 0.02em;
  color: var(--color-muted);
  opacity: 0;
  transition: opacity 0.4s ease;
  pointer-events: none;
}

.intro__skip--on {
  opacity: 0.7;
}

@keyframes intro-node-in {
  0% {
    transform: translate(var(--tx, 0), var(--ty, 0)) scale(0.35);
    opacity: 0;
  }
  45% {
    transform: translate(
        calc(var(--tx, 0px) * 0.45 + var(--ax, 0px)),
        calc(var(--ty, 0px) * 0.45 + var(--ay, 0px))
      )
      scale(0.8);
    opacity: 1;
  }
  100% {
    transform: translate(0, 0) scale(1);
    opacity: 1;
  }
}

@keyframes intro-hub-in {
  from {
    transform: scale(0.55);
    opacity: 0;
  }
  to {
    transform: scale(1);
    opacity: 1;
  }
}

@keyframes intro-fill {
  from {
    transform: scale(0);
  }
  to {
    transform: scale(1);
  }
}

@keyframes intro-flash {
  0% {
    opacity: 0;
  }
  40% {
    opacity: 0.7;
  }
  100% {
    opacity: 0;
  }
}

@keyframes intro-edge-draw {
  from {
    stroke-dashoffset: 100;
  }
  to {
    stroke-dashoffset: 0;
  }
}

@keyframes intro-shock-a {
  0% {
    transform: scale(1);
    opacity: 0.55;
  }
  100% {
    transform: scale(4.2);
    opacity: 0;
  }
}

@keyframes intro-shock-b {
  0% {
    transform: scale(1);
    opacity: 0.35;
  }
  100% {
    transform: scale(5.6);
    opacity: 0;
  }
}

@keyframes intro-wash {
  from {
    opacity: 0;
  }
  to {
    opacity: 0.7;
  }
}

@keyframes intro-particles-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@keyframes intro-word-in {
  from {
    opacity: 0;
    letter-spacing: 0.3em;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    letter-spacing: 0.02em;
    transform: translateY(0);
  }
}
</style>
