<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import BrandLogo from './BrandLogo.vue'
import { CENTER as center, SATELLITES } from './constellation'

// Full-screen brand intro played every time the landing route mounts: the seven
// constellation balls converge, links draw between them, the accent cores ignite
// in a sweep, and the wordmark surfaces. When the timeline completes naturally,
// the assembled glyph docks into the hero constellation's slot (measured at
// runtime) while the backdrop clears, so it reads as one continuous motion that
// hands off to the real hero. User input instead fast-forwards to a quick fade.
// The geometry mirrors AgentConstellation so the docked glyph and the hero are
// pixel-identical at the swap.
//
// The caller only mounts this when motion is allowed, so there is no
// reduced-motion path here beyond the global stylesheet freeze.

const props = defineProps<{ target?: HTMLElement | null }>()
// `reveal` tells the parent to show the hero; `done` tells it to unmount this
// overlay. On a dock they fire together (seamless swap); on a plain fade `reveal`
// fires first so the hero is visible beneath the fading curtain.
const emit = defineEmits<{ reveal: []; done: [] }>()

const { t } = useI18n()

// Distance each ball travels inward along its own radius as it converges.
const CONVERGE = 130

const nodes = SATELLITES.map((n) => ({
  ...n,
  // Start pushed outward along the radial, then slide home.
  tx: Math.round(n.cos * CONVERGE),
  ty: Math.round(n.sin * CONVERGE),
  // Beat offsets (seconds) — converge, then ignite a touch later.
  inDelay: 0.08 + n.id * 0.05,
  drawDelay: 0.42 + n.id * 0.04,
  fillDelay: 0.62 + n.id * 0.05,
}))

// Timeline anchors (ms). The body plays until the entrance chrome has settled,
// then either docks into the hero or, on skip/no-target, fades out. DOCK_MS and
// FADE_MS are bound to the CSS exit transitions via custom properties (below), so
// each duration has a single source of truth shared by the timer and the
// stylesheet.
const BODY_MS = 1200
const DOCK_MS = 560
const FADE_MS = 300
// Skip hint appears partway in, so a returning visitor has an obvious exit.
const HINT_MS = 550

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
    <div class="intro__stage">
      <svg
        ref="svgEl"
        class="intro__svg"
        viewBox="0 0 400 400"
        focusable="false"
        :style="dockStyle"
      >
        <!-- Links: drawn from the hub outward once the balls are nearly home. -->
        <g class="intro__edges">
          <line
            v-for="node in nodes"
            :key="`edge-${node.id}`"
            class="intro-edge"
            :x1="center.x"
            :y1="center.y"
            :x2="node.x"
            :y2="node.y"
            :style="{ '--len': `${node.len}`, animationDelay: `${node.drawDelay}s` }"
          />
        </g>

        <!-- Satellite balls: converge inward, then their cores ignite. -->
        <g
          v-for="node in nodes"
          :key="`node-${node.id}`"
          class="intro-node"
          :style="{ '--tx': `${node.tx}px`, '--ty': `${node.ty}px`, animationDelay: `${node.inDelay}s` }"
        >
          <circle
            class="intro-node__shell"
            :cx="node.x"
            :cy="node.y"
            :r="node.r"
          />
          <circle
            class="intro-node__fill"
            :cx="node.x"
            :cy="node.y"
            :r="node.r"
            :style="{ animationDelay: `${node.fillDelay}s` }"
          />
        </g>

        <!-- Central hub: the seed of the formation, with an expanding ping. -->
        <circle
          class="intro-hub__ping"
          :cx="center.x"
          :cy="center.y"
          r="16"
        />
        <g class="intro-node intro-hub">
          <circle
            class="intro-node__shell"
            :cx="center.x"
            :cy="center.y"
            r="15"
          />
          <circle
            class="intro-node__fill"
            :cx="center.x"
            :cy="center.y"
            r="15"
            :style="{ animationDelay: '0.55s' }"
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
  /* `backwards` (not `both`): reverts to the base 0.7 once done, so the dock-exit
     opacity transition above is not blocked by a held animation value. */
  animation: intro-wash 1s ease-out backwards;
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
.intro-hub__ping {
  transform-box: fill-box;
  transform-origin: center;
}

.intro-node {
  animation: intro-node-in 0.55s cubic-bezier(0.34, 1.4, 0.64, 1) both;
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

.intro-edge {
  stroke: var(--color-accent);
  stroke-width: 2;
  stroke-linecap: round;
  stroke-dasharray: var(--len);
  filter: drop-shadow(0 0 3px var(--color-accent));
  animation: intro-edge-draw 0.42s ease-out both;
}

.intro-hub__ping {
  fill: none;
  stroke: var(--color-accent);
  stroke-width: 1.5;
  animation: intro-ping 1s ease-out 0.7s both;
}

.intro__word {
  /* `backwards`: hidden during the delay, reverts to base once done so the
     dock-exit fade can take over. */
  animation: intro-word-in 0.45s ease-out 0.7s backwards;
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
    transform: translate(var(--tx, 0), var(--ty, 0)) scale(0.4);
    opacity: 0;
  }
  60% {
    opacity: 1;
  }
  100% {
    transform: translate(0, 0) scale(1);
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

@keyframes intro-edge-draw {
  from {
    stroke-dashoffset: var(--len);
  }
  to {
    stroke-dashoffset: 0;
  }
}

@keyframes intro-ping {
  0% {
    transform: scale(1);
    opacity: 0.5;
  }
  70%,
  100% {
    transform: scale(2.4);
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

@keyframes intro-word-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
