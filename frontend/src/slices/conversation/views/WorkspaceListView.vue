<template>
  <section class="workspaces">
    <header>
      <h1>{{ $t('conversation.workspaces.title') }}</h1>
      <form @submit.prevent="onCreate">
        <input
          v-model="newName"
          required
          minlength="1"
          maxlength="80"
        >
        <button
          type="submit"
          :disabled="createMutation.isPending.value"
        >
          {{ $t('conversation.workspaces.create') }}
        </button>
      </form>
    </header>
    <ul v-if="query.data.value">
      <li
        v-for="ws in query.data.value"
        :key="ws.id"
      >
        <router-link
          :to="{ name: 'conversation.chatrooms', params: { workspaceId: ws.id } }"
        >
          {{ ws.name }}
        </router-link>
        <button @click="onDelete(ws.id)">
          {{ $t('conversation.workspaces.delete') }}
        </button>
      </li>
    </ul>
    <p v-if="query.isLoading.value">
      {{ $t('conversation.workspaces.loading') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { ref } from 'vue'
import { useRoute } from 'vue-router'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import {
  createWorkspace,
  deleteWorkspace,
  listWorkspaces,
} from '../api'
import { convKeys } from '../queries'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const projectId = route.params.projectId as string
const newName = ref('')
const toast = useToast()

const query = useQuery({
  queryKey: convKeys.workspaces(projectId),
  queryFn: () => listWorkspaces(projectId),
})

const createMutation = useMutation({
  mutationFn: (name: string) => createWorkspace(projectId, { name }),
  onSuccess: () => qc.invalidateQueries({ queryKey: convKeys.workspaces(projectId) }),
  onError: () => toast.error(t('conversation.workspaces.createFailed')),
})

async function onCreate(): Promise<void> {
  if (!newName.value.trim()) return
  await createMutation.mutateAsync(newName.value.trim())
  newName.value = ''
}

async function onDelete(id: string): Promise<void> {
  await deleteWorkspace(id)
  await qc.invalidateQueries({ queryKey: convKeys.workspaces(projectId) })
}
</script>
