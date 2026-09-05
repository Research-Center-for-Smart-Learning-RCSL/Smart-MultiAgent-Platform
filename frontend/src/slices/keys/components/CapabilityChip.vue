<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SBadge } from '@shared/ui'
import { CAPABILITIES, type ApiKeyProvider } from '../api/keys'

const DISPLAY_NAMES: Record<ApiKeyProvider, string> = {
  claude: 'Claude',
  openai: 'OpenAI',
  gemini: 'Gemini',
  voyage: 'Voyage',
  cohere: 'Cohere',
  openai_compat: '',
}

const CAP_LABELS: Record<string, string> = {
  llm_chat: 'llm',
  embedding: 'embed',
  rerank: 'rerank',
}

const { t } = useI18n()
const props = defineProps<{ provider: ApiKeyProvider }>()
const caps = computed(() => CAPABILITIES[props.provider])
const displayName = computed(() =>
  props.provider === 'openai_compat'
    ? t('keys.providers.openai_compat')
    : DISPLAY_NAMES[props.provider],
)
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
