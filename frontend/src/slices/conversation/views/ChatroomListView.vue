<template>
  <section class="chatrooms">
    <header>
      <h1>{{ $t('conversation.chatrooms.title') }}</h1>
      <form @submit.prevent="onCreate">
        <input
          v-model="newName"
          required
          minlength="1"
          maxlength="80"
        >
        <button type="submit">
          {{ $t('conversation.chatrooms.create') }}
        </button>
      </form>
    </header>
    <ul v-if="query.data.value">
      <li
        v-for="room in query.data.value"
        :key="room.id"
      >
        <router-link
          :to="{ name: 'conversation.chatroom', params: { chatroomId: room.id } }"
        >
          {{ room.name }}
        </router-link>
        <router-link
          :to="{ name: 'conversation.chatroom.settings', params: { chatroomId: room.id } }"
        >
          ⚙
        </router-link>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ref } from 'vue'
import { useRoute } from 'vue-router'

import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { createChatroom, listChatrooms } from '../api'
import { convKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const workspaceId = route.params.workspaceId as string
const newName = ref('')

const query = useQuery({
  queryKey: convKeys.chatrooms(workspaceId),
  queryFn: () => listChatrooms(workspaceId),
})

const createMutation = useMutation({
  mutationFn: (name: string) => createChatroom(workspaceId, { name }),
  onSuccess: () => qc.invalidateQueries({ queryKey: convKeys.chatrooms(workspaceId) }),
  onError: () => ElMessage.error(t('conversation.chatrooms.createFailed')),
})

async function onCreate(): Promise<void> {
  if (!newName.value.trim()) return
  await createMutation.mutateAsync(newName.value.trim())
  newName.value = ''
}
</script>
