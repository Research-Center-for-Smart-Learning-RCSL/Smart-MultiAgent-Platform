<template>
  <form
    class="composer"
    @submit.prevent="$emit('submit')"
  >
    <textarea
      :value="modelValue"
      :placeholder="t('conversation.chatroom.composerPlaceholder')"
      :aria-label="t('conversation.chatroom.composerPlaceholder')"
      @input="onInput"
      @dragover.prevent
      @drop.prevent="$emit('drop', $event)"
    />
    <ul
      v-if="pendingUploads > 0"
      class="attachments"
    >
      <slot name="pending-uploads" />
    </ul>
    <button type="submit">
      {{ t('conversation.chatroom.send') }}
    </button>
  </form>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps<{
  modelValue: string
  pendingUploads: number
  disabled?: boolean
}>()

const emit = defineEmits<{
  submit: []
  typing: []
  drop: [event: DragEvent]
  'update:modelValue': [value: string]
}>()

function onInput(e: Event): void {
  const target = e.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
  emit('typing')
}
</script>

<!-- Grid placement (.composer) is handled by the parent's scoped styles. -->
