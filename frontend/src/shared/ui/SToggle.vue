<script setup lang="ts">
const props = withDefaults(defineProps<{
  modelValue?: boolean
  disabled?: boolean
  size?: 'sm' | 'md'
  variant?: 'switch' | 'robot'
  id?: string | undefined
}>(), {
  modelValue: false,
  disabled: false,
  size: 'md',
  variant: 'switch',
  id: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

function toggle() {
  if (props.disabled) return
  emit('update:modelValue', !props.modelValue)
}
</script>

<template>
  <div
    class="s-toggle"
    :class="{ 's-toggle--disabled': disabled }"
  >
    <button
      :id="id"
      type="button"
      role="switch"
      :aria-checked="modelValue"
      :disabled="disabled"
      class="s-toggle__track"
      :class="[
        variant === 'robot' ? 's-toggle__track--robot' : `s-toggle__track--${size}`,
        { 's-toggle__track--on': modelValue },
      ]"
      @click="toggle"
    >
      <!-- Plain switch: a sliding knob. -->
      <span
        v-if="variant === 'switch'"
        class="s-toggle__knob"
        :class="[
          `s-toggle__knob--${size}`,
          { 's-toggle__knob--on': modelValue },
        ]"
        aria-hidden="true"
      />

      <!-- Robot variant: an AI mascot that wakes on, sleeps off. The white body
           pops on both the grey (off) and accent (on) track. State is carried by
           the .s-robot--on class so reduced-motion (which collapses transition
           duration) still settles on a correct static frame. -->
      <span
        v-else
        class="s-toggle__robot-slot"
        :class="{ 's-toggle__robot-slot--on': modelValue }"
        aria-hidden="true"
      >
        <svg
          class="s-robot"
          :class="{ 's-robot--on': modelValue }"
          viewBox="0 0 24 24"
          focusable="false"
        >
          <line
            class="s-robot__antenna"
            x1="12"
            y1="5"
            x2="12"
            y2="2.6"
          />
          <circle
            class="s-robot__led"
            cx="12"
            cy="2"
            r="1.4"
          />
          <circle
            class="s-robot__bolt"
            cx="3.6"
            cy="12.5"
            r="1.3"
          />
          <circle
            class="s-robot__bolt"
            cx="20.4"
            cy="12.5"
            r="1.3"
          />
          <rect
            class="s-robot__head"
            x="4"
            y="5"
            width="16"
            height="15"
            rx="5"
            ry="5"
          />
          <circle
            class="s-robot__eye"
            cx="9"
            cy="11.5"
            r="2"
          />
          <circle
            class="s-robot__eye"
            cx="15"
            cy="11.5"
            r="2"
          />
          <path
            class="s-robot__mouth"
            d="M9 15.6 Q12 17.6 15 15.6"
          />
        </svg>
      </span>
    </button>
    <span
      v-if="$slots.default"
      class="s-toggle__label"
    >
      <slot />
    </span>
  </div>
</template>

<style scoped>
.s-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
}

.s-toggle--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.s-toggle__track {
  position: relative;
  display: flex;
  align-items: center;
  border: 1.5px solid color-mix(in srgb, var(--color-border), var(--color-fg) 12%);
  border-radius: var(--radius-full);
  background: var(--color-border);
  cursor: pointer;
  padding: 0;
  transition:
    background var(--transition-fast),
    border-color var(--transition-fast);
}

.s-toggle__track--md {
  width: 36px;
  height: 20px;
}

.s-toggle__track--sm {
  width: 28px;
  height: 16px;
}

.s-toggle__track--robot {
  width: 56px;
  height: 30px;
}

.s-toggle__track--on {
  background: var(--color-accent);
  border-color: var(--color-accent);
}

.s-toggle__track:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.s-toggle__track:disabled {
  cursor: not-allowed;
}

.s-toggle__knob {
  position: absolute;
  border-radius: var(--radius-full);
  background: #fff;
  box-shadow: var(--shadow-sm);
  transition: transform var(--transition-fast);
}

.s-toggle__knob--md {
  width: 14px;
  height: 14px;
  left: 2px;
}

.s-toggle__knob--md.s-toggle__knob--on {
  transform: translateX(16px);
}

.s-toggle__knob--sm {
  width: 10px;
  height: 10px;
  left: 2px;
}

.s-toggle__knob--sm.s-toggle__knob--on {
  transform: translateX(12px);
}

.s-toggle__label {
  font-size: 0.875rem;
  color: var(--color-fg);
  user-select: none;
}

/* ─── Robot variant ───────────────────────────────────────────────
   The mascot slides like a knob; eyes, antenna LED and mouth animate to
   read as sleep (off) -> wake (on). Geometry: 24px body, 3px inset both
   ends, 56px track -> 26px travel. A gentle overshoot on the slide is the
   only "spring" in the app's motion vocabulary, kept to this hero toggle.
   ─────────────────────────────────────────────────────────────── */
.s-toggle__robot-slot {
  position: absolute;
  left: 3px;
  width: 24px;
  height: 24px;
  transition: transform var(--transition-normal) cubic-bezier(0.34, 1.56, 0.64, 1);
}

.s-toggle__robot-slot--on {
  transform: translateX(26px);
}

.s-robot {
  width: 24px;
  height: 24px;
  overflow: visible;
}

/* Each animated part transforms about its own centre, not the SVG origin. */
.s-robot__eye,
.s-robot__led {
  transform-box: fill-box;
  transform-origin: center;
}

/* White body keeps contrast on both track colors; outline + bolts carry the
   off(muted) -> on(accent) shift. */
.s-robot__head {
  fill: #fff;
  stroke: var(--color-muted);
  stroke-width: 1.3;
  transition: stroke var(--transition-fast);
}

.s-robot--on .s-robot__head {
  stroke: var(--color-accent);
}

.s-robot__bolt {
  fill: var(--color-muted);
  transition: fill var(--transition-fast);
}

.s-robot--on .s-robot__bolt {
  fill: var(--color-accent);
}

/* Eyes: squeezed near-shut (asleep) -> round and lit (awake). */
.s-robot__eye {
  fill: var(--color-muted);
  transform: scaleY(0.16);
  transition:
    transform var(--transition-normal) ease,
    fill var(--transition-fast);
}

.s-robot--on .s-robot__eye {
  fill: var(--color-accent);
  transform: scaleY(1);
  filter: drop-shadow(0 0 1.5px color-mix(in srgb, var(--color-accent), transparent 30%));
}

/* Smile fades in on wake. */
.s-robot__mouth {
  fill: none;
  stroke: var(--color-accent);
  stroke-width: 1.2;
  stroke-linecap: round;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.s-robot--on .s-robot__mouth {
  opacity: 1;
}

/* Antenna + LED: dim grey asleep, purple glow + slow pulse awake. The pulse
   rests lit (0/100% = full opacity), so the reduced-motion collapse to the
   final frame leaves the LED on. */
.s-robot__antenna {
  stroke: var(--color-muted);
  stroke-width: 1.3;
  stroke-linecap: round;
  transition: stroke var(--transition-fast);
}

.s-robot--on .s-robot__antenna {
  stroke: var(--color-accent-2);
}

.s-robot__led {
  fill: var(--color-muted);
  transition: fill var(--transition-fast);
}

.s-robot--on .s-robot__led {
  fill: var(--color-accent-2);
  filter: drop-shadow(0 0 2px var(--color-accent-2));
  animation: s-robot-led 2s ease-in-out infinite;
}

@keyframes s-robot-led {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

/* One-shot hop the instant the robot wakes. */
.s-robot--on {
  animation: s-robot-hop var(--transition-slow) ease-out;
}

@keyframes s-robot-hop {
  0% {
    transform: translateY(0);
  }
  35% {
    transform: translateY(-3px);
  }
  62% {
    transform: translateY(0);
  }
  80% {
    transform: translateY(-1px);
  }
  100% {
    transform: translateY(0);
  }
}
</style>
