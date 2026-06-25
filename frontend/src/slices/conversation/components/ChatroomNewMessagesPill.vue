<template>
  <button
    type="button"
    class="new-pill"
    @click="emit('click')"
  >
    <ChevronDownIcon class="new-pill__icon" />
    {{ count > 1
      ? t('conversation.chatroom.newMessagesCount', { count })
      : t('conversation.chatroom.newMessages') }}
  </button>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { ChevronDownIcon } from '@heroicons/vue/24/outline'

defineProps<{
  count: number
}>()

const emit = defineEmits<{
  click: []
}>()

const { t } = useI18n()
</script>

<style scoped>
.new-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 16px;
  border: none;
  border-radius: var(--radius-full);
  background: var(--color-accent);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  box-shadow: var(--shadow-md);
  animation: pill-in 150ms ease;
}

.new-pill__icon {
  width: 16px;
  height: 16px;
}

@keyframes pill-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
