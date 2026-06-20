<template>
  <aside class="presence">
    <h4>{{ t('conversation.chatroom.online') }}</h4>
    <ul>
      <li
        v-for="uid in presenceList"
        :key="uid"
      >
        {{ uid.slice(0, 8) }}
      </li>
    </ul>
    <p v-if="agentThinking">
      {{ t('conversation.chatroom.agentThinking') }}
    </p>
  </aside>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps<{
  presenceList: string[]
  agentThinking: string | null
}>()
</script>

<!-- Grid placement (.presence) is handled by the parent's scoped styles. -->
