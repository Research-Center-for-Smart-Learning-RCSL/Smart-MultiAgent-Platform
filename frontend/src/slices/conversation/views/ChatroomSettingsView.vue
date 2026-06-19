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
        class="btn"
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
            class="btn btn-sm"
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
          class="btn btn-primary"
          :disabled="saving"
        >
          {{ $t('conversation.settings.save') }}
        </button>
        <button
          type="button"
          class="btn btn-danger"
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
            class="btn btn-primary"
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
              class="btn btn-danger btn-sm"
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
            class="btn btn-danger btn-sm"
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
import { onMounted, ref, watchEffect } from 'vue'
import { useRoute } from 'vue-router'

import { useI18n } from 'vue-i18n'
import {
  getGuestLink,
  listChatrooms,
} from '../api'
import { DlqViewer, WakeupConfigEditor } from '@slices/workflow'
import { useChatroomSettings } from '../composables/useChatroomSettings'
import { useChatroomBindings } from '../composables/useChatroomBindings'

const { t } = useI18n()
const route = useRoute()
const chatroomId = route.params.chatroomId as string

const guestUrl = ref('')

// ---- room CRUD composable -------------------------------------------------

const {
  name,
  flags,
  room,
  loading,
  loadError,
  saving,
  saveError,
  loadRoom: loadRoomBase,
  onSave,
  onDelete,
} = useChatroomSettings(chatroomId)

// ---- agent binding composable ---------------------------------------------

const {
  selectedAgentId,
  bindingBusy,
  bindingError,
  boundAgents,
  availableAgents,
  orphanAgentIds,
  loadBindings,
  onAddAgent,
  onRemoveAgent,
  saveWakeupConfig,
} = useChatroomBindings(chatroomId, () => room.value)

// ---- orchestrate load with bindings ---------------------------------------

async function loadRoom(): Promise<void> {
  await loadRoomBase()
  if (room.value) void loadBindings()
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
