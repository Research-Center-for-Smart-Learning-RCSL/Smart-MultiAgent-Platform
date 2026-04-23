<template>
  <section class="chatroom-settings" v-if="room">
    <h1>{{ $t('conversation.settings.title') }}</h1>
    <form @submit.prevent="onSave">
      <label>
        {{ $t('conversation.settings.name') }}
        <input v-model="name" required maxlength="80" />
      </label>
      <fieldset>
        <legend>{{ $t('conversation.settings.access') }}</legend>
        <label>
          <input type="checkbox" v-model="flags.allow_org_members" />
          allow_org_members
        </label>
        <label>
          <input type="checkbox" v-model="flags.allow_project_members" />
          allow_project_members
        </label>
        <label>
          <input type="checkbox" v-model="flags.allow_project_owners_only" />
          allow_project_owners_only
        </label>
        <label>
          <input type="checkbox" v-model="flags.allow_guest_links" />
          allow_guest_links
        </label>
        <!--
          UI-side auto-correct per R13.04: `allow_project_owners_only`
          supersedes the member flags — we gray them out but the backend
          accepts any subset.
        -->
      </fieldset>
      <section v-if="flags.allow_guest_links">
        <p>{{ $t('conversation.settings.guestLinkLabel') }}</p>
        <input readonly :value="guestUrl" />
        <button type="button" @click="copyGuest">copy</button>
      </section>
      <button type="submit">{{ $t('conversation.settings.save') }}</button>
      <button type="button" @click="onDelete">
        {{ $t('conversation.settings.delete') }}
      </button>
    </form>

    <!-- Agent binding panel — wake-up config editor + DLQ viewer (G.10).
         Agents are bound via Phase H workspace_agents; this panel renders
         once binding data is available. Until then it stays hidden. -->
    <section v-if="boundAgents.length" class="agent-bindings mt-4">
      <h2 class="font-semibold mb-2">{{ $t('conversation.settings.agentBindings') }}</h2>
      <div v-for="agent in boundAgents" :key="agent.id" class="mb-4">
        <p class="font-medium text-sm mb-1">{{ agent.name ?? agent.id.slice(0, 8) }}</p>
        <WakeupConfigEditor
          v-if="agent.wakeup_config"
          :model-value="agent.wakeup_config"
          @update:model-value="(v) => saveWakeupConfig(agent.id, v)"
        />
        <!-- DLQ viewer per agent — room owner / admin only (G.10). -->
        <DlqViewer :agent-id="agent.id" />
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { useQueryClient } from '@tanstack/vue-query'
import { reactive, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ElMessage } from 'element-plus'
import { deleteChatroom, getGuestLink, listChatrooms, patchChatroom } from '../api'
import { convKeys } from '../queries'
import { DlqViewer, WakeupConfigEditor, patchAgentWakeupConfig } from '@slices/workflow'
import type { WakeupConfig } from '@slices/workflow'

const route = useRoute()
const router = useRouter()
const qc = useQueryClient()
const chatroomId = route.params.chatroomId as string

const name = ref('')
const flags = reactive({
  allow_org_members: false,
  allow_project_members: true,
  allow_project_owners_only: false,
  allow_guest_links: false,
})
const room = ref<import('../types').Chatroom | null>(null)
const guestUrl = ref('')

// Agent bindings (populated by Phase H workspace_agents; empty until then).
interface BoundAgent {
  id: string
  name?: string
  wakeup_config?: WakeupConfig
}
const boundAgents = ref<BoundAgent[]>([])

async function saveWakeupConfig(agentId: string, config: WakeupConfig): Promise<void> {
  try {
    await patchAgentWakeupConfig(agentId, config)
    ElMessage.success('Wakeup config saved.')
  } catch {
    ElMessage.error('Failed to save wakeup config.')
  }
}

watchEffect(async () => {
  // The single-room GET is trivially derivable from the chatrooms query if
  // the parent list is cached; otherwise call the list endpoint by
  // workspace. We don't have workspace id in the URL here, so rely on
  // cache lookup by id across every cached list.
  const caches = qc.getQueriesData<import('../types').Chatroom[]>({
    queryKey: ['conversation', 'chatrooms'],
  })
  for (const [, data] of caches) {
    if (!data) continue
    const found = data.find((r) => r.id === chatroomId)
    if (found) {
      room.value = found
      name.value = found.name
      flags.allow_org_members = found.allow_org_members
      flags.allow_project_members = found.allow_project_members
      flags.allow_project_owners_only = found.allow_project_owners_only
      flags.allow_guest_links = found.allow_guest_links
      break
    }
  }
  // Refresh guest link lazily only when visible.
  if (flags.allow_guest_links && room.value) {
    try {
      const link = await getGuestLink(chatroomId)
      guestUrl.value = link.url
    } catch {
      guestUrl.value = ''
    }
  }
})

async function onSave(): Promise<void> {
  if (!room.value) return
  await patchChatroom(chatroomId, room.value.version, {
    name: name.value,
    ...flags,
  })
  await qc.invalidateQueries({ queryKey: ['conversation', 'chatrooms'] })
}

async function onDelete(): Promise<void> {
  await deleteChatroom(chatroomId)
  await qc.invalidateQueries({ queryKey: ['conversation', 'chatrooms'] })
  router.back()
}

function copyGuest(): void {
  navigator.clipboard?.writeText(guestUrl.value).catch(() => {})
}

// Keep workspace-list cache warm so the R13.02 auto-recreate-default
// invariant shows up here after a delete.
watchEffect(() => {
  if (!room.value) return
  void listChatrooms(room.value.workspace_id)
})
</script>
