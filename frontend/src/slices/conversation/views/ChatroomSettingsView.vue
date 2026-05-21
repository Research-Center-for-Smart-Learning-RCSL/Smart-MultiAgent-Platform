<template>
  <main class="chatroom-settings">
    <h1>{{ $t('conversation.settings.title') }}</h1>

    <p v-if="loading">
      {{ $t('conversation.settings.loading') }}
    </p>

    <p
      v-else-if="loadError || !room"
      role="alert"
      class="error"
    >
      {{ $t('conversation.settings.loadFailed') }}
      <button
        type="button"
        @click="loadRoom"
      >
        {{ $t('conversation.settings.retry') }}
      </button>
    </p>

    <template v-else>
      <form @submit.prevent="onSave">
        <label>
          {{ $t('conversation.settings.name') }}
          <input
            v-model="name"
            required
            maxlength="80"
          >
        </label>
        <fieldset>
          <legend>{{ $t('conversation.settings.access') }}</legend>
          <label>
            <input
              v-model="flags.allow_org_members"
              type="checkbox"
            >
            {{ $t('conversation.settings.allowOrgMembers') }}
          </label>
          <label>
            <input
              v-model="flags.allow_project_members"
              type="checkbox"
            >
            {{ $t('conversation.settings.allowProjectMembers') }}
          </label>
          <label>
            <input
              v-model="flags.allow_project_owners_only"
              type="checkbox"
            >
            {{ $t('conversation.settings.allowProjectOwnersOnly') }}
          </label>
          <label>
            <input
              v-model="flags.allow_guest_links"
              type="checkbox"
            >
            {{ $t('conversation.settings.allowGuestLinks') }}
          </label>
          <!--
            UI-side auto-correct per R13.04: `allow_project_owners_only`
            supersedes the member flags — we gray them out but the backend
            accepts any subset.
          -->
        </fieldset>
        <section v-if="flags.allow_guest_links">
          <p>{{ $t('conversation.settings.guestLinkLabel') }}</p>
          <input
            readonly
            :value="guestUrl"
          >
          <button
            type="button"
            @click="copyGuest"
          >
            {{ $t('conversation.settings.copy') }}
          </button>
        </section>
        <p
          v-if="saveError"
          role="alert"
          class="error"
        >
          {{ $t(saveError) }}
        </p>
        <button
          type="submit"
          :disabled="saving"
        >
          {{ $t('conversation.settings.save') }}
        </button>
        <button
          type="button"
          :disabled="saving"
          @click="onDelete"
        >
          {{ $t('conversation.settings.delete') }}
        </button>
      </form>

      <!-- Agent binding panel — wake-up config editor + DLQ viewer (G.10).
           Agents are bound via Phase H workspace_agents; this panel renders
           once binding data is available. Until then it stays hidden. -->
      <section
        v-if="boundAgents.length"
        class="agent-bindings mt-4"
      >
        <h2 class="font-semibold mb-2">
          {{ $t('conversation.settings.agentBindings') }}
        </h2>
        <div
          v-for="agent in boundAgents"
          :key="agent.id"
          class="mb-4"
        >
          <p class="font-medium text-sm mb-1">
            {{ agent.name ?? agent.id.slice(0, 8) }}
          </p>
          <WakeupConfigEditor
            v-if="agent.wakeup_config"
            :model-value="agent.wakeup_config"
            @update:model-value="(v) => saveWakeupConfig(agent.id, v)"
          />
          <!-- DLQ viewer per agent — room owner / admin only (G.10). -->
          <DlqViewer :agent-id="agent.id" />
        </div>
      </section>
    </template>
  </main>
</template>

<script setup lang="ts">
import { useQueryClient } from '@tanstack/vue-query'
import { onMounted, reactive, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import {
  deleteChatroom,
  getChatroom,
  getGuestLink,
  listChatrooms,
  patchChatroom,
} from '../api'
import { DlqViewer, WakeupConfigEditor, patchAgentWakeupConfig } from '@slices/workflow'
import type { WakeupConfig } from '@slices/workflow'
import type { Chatroom } from '../types'

const { t } = useI18n()
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
const room = ref<Chatroom | null>(null)
const guestUrl = ref('')

const loading = ref(true)
const loadError = ref(false)
const saving = ref(false)
// i18n key for the inline save error, or null when the form is clean.
const saveError = ref<string | null>(null)

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
    ElMessage.success(t('conversation.settings.wakeupConfigSaved'))
  } catch {
    ElMessage.error(t('conversation.settings.wakeupConfigFailed'))
  }
}

/** Copy a chatroom into the form fields. */
function applyRoom(found: Chatroom): void {
  room.value = found
  name.value = found.name
  flags.allow_org_members = found.allow_org_members
  flags.allow_project_members = found.allow_project_members
  flags.allow_project_owners_only = found.allow_project_owners_only
  flags.allow_guest_links = found.allow_guest_links
}

/** Find this chatroom in any cached `['conversation','chatrooms']` list. */
function findInCache(): Chatroom | null {
  const caches = qc.getQueriesData<Chatroom[]>({
    queryKey: ['conversation', 'chatrooms'],
  })
  for (const [, data] of caches) {
    const found = data?.find((r) => r.id === chatroomId)
    if (found) return found
  }
  return null
}

async function loadRoom(): Promise<void> {
  loading.value = true
  loadError.value = false
  // Prefer a warm cache, but a deep link straight to settings has none —
  // in that case fetch the single chatroom so `room` is never stuck null.
  const cached = findInCache()
  if (cached) {
    applyRoom(cached)
    loading.value = false
    return
  }
  try {
    applyRoom(await getChatroom(chatroomId))
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function onSave(): Promise<void> {
  if (!room.value || saving.value) return
  saving.value = true
  saveError.value = null
  try {
    applyRoom(
      await patchChatroom(chatroomId, room.value.version, {
        name: name.value,
        ...flags,
      }),
    )
    await qc.invalidateQueries({ queryKey: ['conversation', 'chatrooms'] })
    ElMessage.success(t('conversation.settings.saved'))
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      // Stale optimistic-concurrency version (another writer won the race).
      // Refresh only `version` so a retry carries the current one — the
      // user's pending edits stay in the form rather than being clobbered.
      saveError.value = 'conversation.settings.versionConflict'
      try {
        room.value = await getChatroom(chatroomId)
      } catch {
        /* keep the form as-is; the inline error already explains the retry */
      }
    } else {
      saveError.value = 'conversation.settings.saveFailed'
    }
  } finally {
    saving.value = false
  }
}

async function onDelete(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('conversation.settings.deleteConfirm'),
      t('conversation.settings.deleteConfirmTitle'),
      { confirmButtonText: t('conversation.settings.delete'), cancelButtonText: t('app.cancel'), type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await deleteChatroom(chatroomId)
  } catch {
    ElMessage.error(t('conversation.settings.deleteFailed'))
    return
  }
  await qc.invalidateQueries({ queryKey: ['conversation', 'chatrooms'] })
  router.back()
}

function copyGuest(): void {
  navigator.clipboard?.writeText(guestUrl.value).catch(() => {})
}

onMounted(loadRoom)

// Refresh the guest link lazily, only while the panel is visible.
watchEffect(async () => {
  if (flags.allow_guest_links && room.value) {
    try {
      const link = await getGuestLink(chatroomId)
      guestUrl.value = link.url
    } catch {
      guestUrl.value = ''
    }
  }
})

// Keep workspace-list cache warm so the R13.02 auto-recreate-default
// invariant shows up here after a delete.
watchEffect(() => {
  if (!room.value) return
  void listChatrooms(room.value.workspace_id)
})
</script>

<style scoped>
.error {
  color: #b91c1c;
}
</style>
