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

      <!-- Agent binding panel — bind project agents to this room, then edit
           each bound agent's wake-up config + view its DLQ (G.10). Agents are
           project-scoped, so we resolve workspace → project before listing. -->
      <section class="agent-bindings mt-4">
        <h2 class="font-semibold mb-2">
          {{ $t('conversation.settings.agentBindings') }}
        </h2>

        <form
          class="agent-add flex items-end gap-2 mb-3"
          @submit.prevent="onAddAgent"
        >
          <label>
            {{ $t('conversation.settings.addAgent') }}
            <select
              v-model="selectedAgentId"
              :disabled="bindingBusy"
            >
              <option
                value=""
                disabled
              >
                {{ $t('conversation.settings.selectAgent') }}
              </option>
              <option
                v-for="a in availableAgents"
                :key="a.id"
                :value="a.id"
              >
                {{ a.name }}
              </option>
            </select>
          </label>
          <button
            type="submit"
            :disabled="!selectedAgentId || bindingBusy"
          >
            {{ $t('conversation.settings.add') }}
          </button>
        </form>

        <p
          v-if="!availableAgents.length && !boundAgents.length && !orphanAgentIds.length"
          class="muted text-sm text-gray-500"
        >
          {{ $t('conversation.settings.noAgents') }}
        </p>

        <p
          v-if="bindingError"
          role="alert"
          class="error"
        >
          {{ $t(bindingError) }}
        </p>

        <div
          v-for="agent in boundAgents"
          :key="agent.id"
          class="mb-4"
        >
          <div class="agent-head flex items-center justify-between gap-2">
            <p class="font-medium text-sm mb-1">
              {{ agent.name ?? agent.id.slice(0, 8) }}
            </p>
            <button
              type="button"
              :disabled="bindingBusy"
              @click="onRemoveAgent(agent.id)"
            >
              {{ $t('conversation.settings.removeAgent') }}
            </button>
          </div>
          <WakeupConfigEditor
            v-if="agent.wakeup_config"
            :model-value="agent.wakeup_config"
            @update:model-value="(v) => saveWakeupConfig(agent.id, v)"
          />
          <!-- DLQ viewer per agent — room owner / admin only (G.10). -->
          <DlqViewer :agent-id="agent.id" />
        </div>

        <!-- Stale bindings whose agent no longer appears in the project list
             (typically soft-deleted) — still removable. -->
        <div
          v-for="id in orphanAgentIds"
          :key="id"
          class="agent-head flex items-center justify-between gap-2 mb-4"
        >
          <p class="font-medium text-sm text-gray-500">
            {{ id.slice(0, 8) }} · {{ $t('conversation.settings.unknownAgent') }}
          </p>
          <button
            type="button"
            :disabled="bindingBusy"
            @click="onRemoveAgent(id)"
          >
            {{ $t('conversation.settings.removeAgent') }}
          </button>
        </div>
      </section>
    </template>
  </main>
</template>

<script setup lang="ts">
import { useQueryClient } from '@tanstack/vue-query'
import { computed, onMounted, reactive, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ElMessageBox } from 'element-plus'
import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import {
  addChatroomAgent,
  deleteChatroom,
  getChatroom,
  getGuestLink,
  getWorkspace,
  listChatroomAgents,
  listChatrooms,
  listProjectAgents,
  patchChatroom,
  removeChatroomAgent,
} from '../api'
import { DlqViewer, WakeupConfigEditor, patchAgentWakeupConfig } from '@slices/workflow'
import type { WakeupConfig } from '@slices/workflow'
import type { Agent } from '@slices/agents'
import type { Chatroom } from '../types'

const { t } = useI18n()
const toast = useToast()
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

// Agent bindings. Chatrooms only carry `workspace_id`, so we resolve the
// parent project, list its agents, and intersect with the room's bound set.
interface BoundAgent {
  id: string
  name?: string
  wakeup_config?: WakeupConfig
}
// All agents in the room's parent project, and the ids currently bound.
const projectAgents = ref<Agent[]>([])
const boundAgentIds = ref<string[]>([])
const selectedAgentId = ref('')
const bindingBusy = ref(false)
// i18n key for an inline binding error, or null when clean.
const bindingError = ref<string | null>(null)

// WakeupConfigEditor dereferences `triggers.{every_n_messages,silence_minutes,
// call_only}.enabled` at setup, so it only accepts a fully-formed config — a
// partial one (e.g. `{triggers:{}}`) would crash the whole panel. Validate the
// three trigger sub-objects are present before handing it over.
function isFullWakeupConfig(raw: unknown): boolean {
  if (!raw || typeof raw !== 'object') return false
  const triggers = (raw as Record<string, unknown>).triggers
  if (!triggers || typeof triggers !== 'object') return false
  const t = triggers as Record<string, unknown>
  return (
    typeof t.every_n_messages === 'object'
    && typeof t.silence_minutes === 'object'
    && typeof t.call_only === 'object'
  )
}

// Return a plain deep clone when the shape is valid, else undefined so the
// editor stays hidden. The source is a reactive proxy and the editor
// structuredClones its model-value (which rejects proxies), so the JSON
// round-trip both unwraps and deep-copies.
function toEditableWakeup(raw: unknown): WakeupConfig | undefined {
  return isFullWakeupConfig(raw)
    ? (JSON.parse(JSON.stringify(raw)) as WakeupConfig)
    : undefined
}

const boundAgents = computed<BoundAgent[]>(() =>
  boundAgentIds.value
    .map((id) => projectAgents.value.find((a) => a.id === id))
    .filter((a): a is Agent => a != null)
    .map((a) => ({
      id: a.id,
      name: a.name,
      wakeup_config: toEditableWakeup(a.wakeup_config),
    })),
)

// Agents bindable but not yet bound (active only).
const availableAgents = computed<Agent[]>(() =>
  projectAgents.value.filter(
    (a) => !a.deleted_at && !boundAgentIds.value.includes(a.id),
  ),
)

// Bound ids with no match in the project list (e.g. the agent was soft-deleted
// after binding — the list endpoint omits it). Surfaced as removable rows so a
// stale binding can still be cleaned up rather than becoming invisible.
const orphanAgentIds = computed<string[]>(() =>
  boundAgentIds.value.filter(
    (id) => !projectAgents.value.some((a) => a.id === id),
  ),
)

/** Resolve project, then load its agents + this room's bound set. */
async function loadBindings(): Promise<void> {
  if (!room.value) return
  bindingError.value = null
  try {
    const ws = await getWorkspace(room.value.workspace_id)
    const [agents, boundIds] = await Promise.all([
      listProjectAgents(ws.project_id),
      listChatroomAgents(chatroomId),
    ])
    projectAgents.value = agents
    boundAgentIds.value = boundIds
  } catch {
    bindingError.value = 'conversation.settings.bindingsLoadFailed'
  }
}

async function onAddAgent(): Promise<void> {
  if (!selectedAgentId.value || bindingBusy.value) return
  bindingBusy.value = true
  bindingError.value = null
  try {
    await addChatroomAgent(chatroomId, selectedAgentId.value)
    selectedAgentId.value = ''
    await loadBindings()
  } catch {
    bindingError.value = 'conversation.settings.bindFailed'
  } finally {
    bindingBusy.value = false
  }
}

async function onRemoveAgent(agentId: string): Promise<void> {
  if (bindingBusy.value) return
  bindingBusy.value = true
  bindingError.value = null
  try {
    await removeChatroomAgent(chatroomId, agentId)
    await loadBindings()
  } catch {
    bindingError.value = 'conversation.settings.unbindFailed'
  } finally {
    bindingBusy.value = false
  }
}

async function saveWakeupConfig(agentId: string, config: WakeupConfig): Promise<void> {
  try {
    await patchAgentWakeupConfig(agentId, config)
    toast.success(t('conversation.settings.wakeupConfigSaved'))
  } catch {
    toast.error(t('conversation.settings.wakeupConfigFailed'))
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
    void loadBindings()
    return
  }
  try {
    applyRoom(await getChatroom(chatroomId))
    void loadBindings()
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
    toast.success(t('conversation.settings.saved'))
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
    toast.error(t('conversation.settings.deleteFailed'))
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
.chatroom-settings h1 {
  font-size: 1.25rem;
  font-weight: 600;
  margin-bottom: 1rem;
}
.error {
  color: var(--color-danger);
}
</style>
