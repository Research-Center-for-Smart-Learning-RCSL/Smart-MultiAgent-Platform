<template>
  <li
    ref="rootRef"
    class="obs-card"
  >
    <div class="obs-card__head">
      <span class="obs-card__agent">{{ agentName }}</span>
      <SRelativeTime
        v-if="observation.created_at"
        class="obs-card__time"
        :value="observation.created_at"
      />
      <span class="obs-card__trigger">{{ triggerLabel }}</span>
    </div>

    <!-- R28.15: blocks when the turn assembled any, the markdown body otherwise.
         Every observation recorded before presentation blocks existed, and every
         turn that did not call the tool, takes the second path unchanged. -->
    <div
      v-if="blocks.length"
      class="obs-card__body"
      :class="{ 'obs-card__body--clamped': clamped }"
    >
      <ObservationBlocks :blocks="blocks">
        <template #prose="{ block }">
          <!-- The one sanitised markdown site on this path. A prose block can sit
               at any position, so the binding is handed down as a scoped slot
               rather than reimplemented in a second file: gate #4's allowlist
               holds this file and adding another would need a security review. -->
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div
            class="obs-card__prose"
            v-html="proseHtml(block)"
          />
        </template>
      </ObservationBlocks>
    </div>

    <!-- Rendered through the shared DOMPurify pipeline; this file is on the
         v-html allowlist in eslint.config.js. -->
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div
      v-else
      class="obs-card__body"
      :class="{ 'obs-card__body--clamped': clamped }"
      v-html="renderedHtml"
    />
    <button
      v-if="canClamp"
      type="button"
      class="obs-card__expand"
      :aria-expanded="!clamped"
      @click="clamped = !clamped"
    >
      {{ clamped ? t('conversation.observers.expand') : t('conversation.observers.collapse') }}
    </button>

    <div class="obs-card__foot">
      <span
        v-if="releaseChip"
        class="obs-card__chip"
      >{{ releaseChip }}</span>
      <div class="obs-card__actions">
        <SButton
          v-if="!observation.released_at"
          size="sm"
          variant="ghost"
          @click="emit('release', observation)"
        >
          {{ t('conversation.observers.release') }}
        </SButton>
        <SButton
          size="sm"
          variant="ghost"
          icon-only
          :aria-label="t('conversation.observers.delete')"
          @click="emit('delete', observation)"
        >
          <TrashIcon class="w-4 h-4" />
        </SButton>
      </div>
    </div>
  </li>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { TrashIcon } from '@heroicons/vue/24/outline'
import { SButton, SRelativeTime } from '@shared/ui'
import ObservationBlocks from './observation-blocks/ObservationBlocks.vue'
import { useMarkdownEnhance } from '../composables/useMarkdownEnhance'
import { renderMarkdown } from '../utils/renderMarkdown'
import type { Observation, ObservationBlock } from '../types'

const props = defineProps<{
  observation: Observation
  agentName: string
}>()

const emit = defineEmits<{
  release: [observation: Observation]
  delete: [observation: Observation]
}>()

const { t } = useI18n()

// F-9. `renderMarkdown` only sanitises; mermaid, KaTeX and highlight.js are a
// separate DOM pass ChatroomView wires once against the message list, and both
// ObserverPanel mounts are outside that subtree — so the preview a release
// decision is made against showed a raw fence for content the feed renders as a
// diagram. Per card rather than per panel: the card owns the `v-html` regions,
// and the composable re-schedules on its own `onUpdated`, which a panel-level
// root would only see when the *list* re-rendered.
//
// Safe as a second call site: the composable holds no cross-instance state and
// all three passes are idempotent by consumption (each skips what it already
// converted). The ref is bound during setup and read only after a 120ms
// debounce, so it is non-null by the time the pass runs.
const rootRef = ref<HTMLElement | null>(null)
useMarkdownEnhance(rootRef)

const blocks = computed<ObservationBlock[]>(() => props.observation.blocks ?? [])

const renderedHtml = computed(() => renderMarkdown(props.observation.content_md))

function proseHtml(block: ObservationBlock): string {
  return renderMarkdown(block.kind === 'prose' ? block.text : '')
}

// Character count is the wrong measure of height once an observation is a stack
// of figures: a nine-cell grid is three rows tall and barely a hundred
// characters. Whichever measure trips first decides.
const CLAMP_CHARS = 600
const CLAMP_BLOCKS = 3

const canClamp = computed(
  () => blocks.value.length > CLAMP_BLOCKS || props.observation.content_md.length > CLAMP_CHARS,
)
const clamped = ref(canClamp.value)

const triggerLabel = computed(() => {
  const key = `conversation.observers.trigger.${props.observation.trigger}`
  const label = t(key)
  // vue-i18n echoes the key back when it is missing; fall back to the raw value.
  return label === key ? props.observation.trigger : label
})

const releaseChip = computed<string | null>(() => {
  const target = props.observation.release_target
  if (!target) return null
  if (target.kind === 'room') return t('conversation.observers.releasedToRoom')
  const base = t('conversation.observers.releasedToAgents', { n: target.agent_ids.length })
  return target.woken ? `${base} ${t('conversation.observers.andWoken')}` : base
})
</script>

<style scoped>
.obs-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  background: var(--color-surface);
  list-style: none;
}

.obs-card__head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--space-1-5);
}

.obs-card__agent {
  font-size: var(--font-size-code);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
}

.obs-card__time,
.obs-card__trigger {
  font-size: 11px;
  color: var(--color-muted);
}

.obs-card__trigger {
  margin-left: auto;
}

.obs-card__body {
  font-size: var(--font-size-code);
  color: var(--color-fg);
  overflow-wrap: anywhere;
}

/* The card's own markdown typography applies to a prose block too, so a `prose`
   at position 3 reads the same as one at position 1. */
.obs-card__prose {
  overflow-wrap: anywhere;
}

.obs-card__body--clamped {
  display: -webkit-box;
  -webkit-line-clamp: 14;
  line-clamp: 14;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.obs-card__expand {
  margin-top: var(--space-1);
  background: none;
  border: none;
  padding: 0;
  color: var(--color-accent);
  font-size: var(--font-size-xs);
  cursor: pointer;
}

.obs-card__foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2-5);
}

.obs-card__chip {
  font-size: 11px;
  color: var(--color-muted);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  padding: 1px 8px;
}

.obs-card__actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin-left: auto;
}
</style>
