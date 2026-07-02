<template>
  <li class="obs-card">
    <div class="obs-card__head">
      <span class="obs-card__agent">{{ agentName }}</span>
      <SRelativeTime
        v-if="observation.created_at"
        class="obs-card__time"
        :value="observation.created_at"
      />
      <span class="obs-card__trigger">{{ triggerLabel }}</span>
    </div>

    <!-- Rendered through the shared DOMPurify pipeline; this file is on the
         v-html allowlist in eslint.config.js. -->
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div
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
import { renderMarkdown } from '../utils/renderMarkdown'
import type { Observation } from '../types'

const props = defineProps<{
  observation: Observation
  agentName: string
}>()

const emit = defineEmits<{
  release: [observation: Observation]
  delete: [observation: Observation]
}>()

const { t } = useI18n()

const renderedHtml = computed(() => renderMarkdown(props.observation.content_md))
const canClamp = computed(() => props.observation.content_md.length > 600)
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
  padding: 12px;
  background: var(--color-surface);
  list-style: none;
}

.obs-card__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}

.obs-card__agent {
  font-size: 13px;
  font-weight: 600;
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
  font-size: 13px;
  color: var(--color-fg);
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
  margin-top: 4px;
  background: none;
  border: none;
  padding: 0;
  color: var(--color-accent);
  font-size: 12px;
  cursor: pointer;
}

.obs-card__foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
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
  gap: 4px;
  margin-left: auto;
}
</style>
