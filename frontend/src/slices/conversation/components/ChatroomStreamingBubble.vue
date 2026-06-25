<template>
  <li
    class="streaming"
    data-testid="streaming-draft"
  >
    <article class="bubble bubble--agent">
      <header class="bubble__meta">
        <SAvatar
          :name="agentName"
          size="sm"
        />
        <span class="bubble__sender">{{ agentName }}</span>
        <span class="streaming__label">{{ t('conversation.chatroom.agentStreaming') }}</span>
      </header>
      <!-- Single sanitiser site (eslint allowlist): html came from renderMarkdown. -->
      <div
        class="bubble__body md streaming-md"
        v-html="html"
      />
    </article>
  </li>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SAvatar } from '@shared/ui'

defineProps<{
  html: string
  agentName: string
}>()

const { t } = useI18n()
</script>

<style scoped>
.streaming {
  margin-bottom: 8px;
}

.bubble {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-accent);
  border-radius: var(--radius-md);
  padding: 12px 16px;
  max-width: 85%;
}

.bubble__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.bubble__sender {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-accent);
}

.streaming__label {
  margin-left: auto;
  font-size: 12px;
  font-style: italic;
  color: var(--color-accent);
}

.bubble__body {
  font-size: 14px;
  line-height: 1.5;
  color: var(--color-fg);
  word-break: break-word;
}

.streaming-md::after {
  content: '_';
  color: var(--color-accent);
  font-weight: 600;
  animation: blink-cursor 1s steps(1) infinite;
}

@keyframes blink-cursor {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
</style>
