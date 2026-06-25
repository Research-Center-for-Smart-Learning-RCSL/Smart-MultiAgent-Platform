<template>
  <p
    class="typing"
    :class="{ 'typing--visible': names.length > 0 }"
  >
    <template v-if="names.length">
      {{ text }}
      <span class="typing__dots">
        <span class="typing__dot" />
        <span class="typing__dot" />
        <span class="typing__dot" />
      </span>
    </template>
  </p>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  names: string[]
}>()

const { t } = useI18n()

const text = computed(() => {
  const n = props.names
  if (n.length === 1) return t('conversation.chatroom.typingOne', { name: n[0] })
  if (n.length === 2) return t('conversation.chatroom.typingTwo', { a: n[0], b: n[1] })
  return t('conversation.chatroom.typingMany', { count: n.length })
})
</script>

<style scoped>
.typing {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 16px;
  font-size: 13px;
  font-style: italic;
  color: var(--color-muted);
  opacity: 0;
  transition: opacity 150ms ease;
}

.typing--visible {
  opacity: 1;
}

.typing__dots {
  display: inline-flex;
  gap: 2px;
}

.typing__dot {
  width: 3px;
  height: 3px;
  border-radius: var(--radius-full);
  background: var(--color-muted);
  animation: typing-dot 1.4s infinite;
}

.typing__dot:nth-child(2) {
  animation-delay: 0.2s;
}

.typing__dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typing-dot {
  0%, 80%, 100% { opacity: 0.3; }
  40% { opacity: 1; }
}
</style>
