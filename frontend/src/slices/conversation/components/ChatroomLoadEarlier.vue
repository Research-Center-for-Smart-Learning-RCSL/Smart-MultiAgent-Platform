<template>
  <div class="load-earlier">
    <SButton
      variant="secondary"
      size="sm"
      :disabled="loading"
      @click="emit('load')"
    >
      <template #icon-left>
        <SLoadingSpinner
          v-if="loading"
          size="sm"
        />
        <ArrowUpIcon
          v-else
          class="w-4 h-4"
        />
      </template>
      {{ loading
        ? t('conversation.chatroom.loadingEarlier')
        : t('conversation.chatroom.loadEarlier') }}
    </SButton>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { ArrowUpIcon } from '@heroicons/vue/24/outline'
import { SButton, SLoadingSpinner } from '@shared/ui'

defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  load: []
}>()

const { t } = useI18n()
</script>

<style scoped>
.load-earlier {
  display: flex;
  justify-content: center;
  padding: 8px 0;
}
</style>
