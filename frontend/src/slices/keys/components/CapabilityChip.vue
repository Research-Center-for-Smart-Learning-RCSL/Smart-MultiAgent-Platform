<script setup lang="ts">
import { computed } from 'vue'
import { SBadge } from '@shared/ui'
import { CAPABILITIES, type ApiKeyProvider } from '../api/keys'

const DISPLAY_NAMES: Record<ApiKeyProvider, string> = {
  claude: 'Claude',
  openai: 'OpenAI',
  gemini: 'Gemini',
  voyage: 'Voyage',
  cohere: 'Cohere',
  openai_compat: 'Custom',
}

const CAP_LABELS: Record<string, string> = {
  llm_chat: 'llm',
  embedding: 'embed',
  rerank: 'rerank',
}

const props = defineProps<{ provider: ApiKeyProvider }>()
const caps = computed(() => CAPABILITIES[props.provider])
const displayName = computed(() => DISPLAY_NAMES[props.provider])
</script>

<template>
  <span class="inline-flex items-center gap-1">
    <span class="text-sm font-medium text-[var(--color-fg)]">{{ displayName }}</span>
    <SBadge
      v-for="c in caps"
      :key="c"
      variant="neutral"
      size="sm"
    >
      {{ CAP_LABELS[c] ?? c }}
    </SBadge>
  </span>
</template>
