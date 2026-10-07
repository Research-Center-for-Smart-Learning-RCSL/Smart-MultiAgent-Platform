<script setup lang="ts">
import { computed } from 'vue'

type Size = 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    name: string
    size?: Size
    src?: string | null
  }>(),
  {
    size: 'md',
    src: null,
  },
)

// The first character a reader sees: names keep emoji, and `charAt(0)` would take
// half of a surrogate pair. Code points are the fallback where Segmenter is missing.
// Built once and shared by every avatar: construction resolves a locale.
let segmenter: Intl.Segmenter | undefined
function firstGrapheme(text: string): string {
  if (typeof Intl.Segmenter === 'function') {
    segmenter ??= new Intl.Segmenter(undefined, { granularity: 'grapheme' })
    const first = segmenter.segment(text)[Symbol.iterator]().next()
    return first.done ? '' : first.value.segment
  }
  return Array.from(text)[0] ?? ''
}

const initial = computed(() => firstGrapheme(props.name).toUpperCase())

const sizeClass = computed(() => `s-avatar--${props.size}`)
</script>

<template>
  <span
    class="s-avatar"
    :class="sizeClass"
    role="img"
    :aria-label="props.name"
  >
    <img
      v-if="props.src"
      :src="props.src"
      :alt="props.name"
      class="s-avatar__img"
    >
    <span
      v-else
      class="s-avatar__initials"
    >
      {{ initial }}
    </span>
  </span>
</template>

<style scoped>
.s-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  border: 1px solid var(--color-border);
  overflow: hidden;
  flex-shrink: 0;
  vertical-align: middle;
}

/* Sizes */
.s-avatar--sm {
  width: 24px;
  height: 24px;
  font-size: var(--font-size-2xs);
}
.s-avatar--md {
  width: 32px;
  height: 32px;
  font-size: var(--font-size-xs);
}
.s-avatar--lg {
  width: 40px;
  height: 40px;
  font-size: var(--font-size-sm);
}

/* Image */
.s-avatar__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* Initials fallback */
.s-avatar__initials {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  background-color: var(--color-accent);
  color: var(--color-on-accent);
  font-weight: var(--weight-semibold);
  line-height: var(--line-none);
  user-select: none;
}
</style>
