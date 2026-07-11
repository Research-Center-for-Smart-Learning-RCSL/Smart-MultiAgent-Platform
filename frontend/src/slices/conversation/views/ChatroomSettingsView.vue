<script setup lang="ts">
import { computed, onMounted, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowLeftIcon,
  ClipboardDocumentIcon,
  TrashIcon,
  ArchiveBoxArrowDownIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SCard,
  SFormField,
  SInput,
  SToggle,
  SSelect,
  SButton,
  SAlert,
  SSkeleton,
  SDivider,
  SWakeupEditor,
} from '@shared/ui'
import { useToast, useConfirmDialog } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { tenancyKeys, projectsApi } from '@slices/tenancy'
import { useQuery } from '@tanstack/vue-query'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import {
  compactChatroom,
  getGuestLink,
  getWorkspace,
  listChatrooms,
} from '../api'
import { DlqViewer } from '@slices/workflow'
import { ConceptMapPanel } from '@slices/agents'
import { useChatroomSettings } from '../composables/useChatroomSettings'
import { useChatroomBindings } from '../composables/useChatroomBindings'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirmDialog()
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
  saveDisclosure,
  onDelete,
} = useChatroomSettings(chatroomId)

// ---- agent binding composable ---------------------------------------------

const {
  selectedAgentId,
  selectedRole,
  bindingBusy,
  bindingError,
  boundAgents,
  availableAgents,
  orphanAgentIds,
  loadBindings,
  onAddAgent,
  onRemoveAgent,
  onSetRole,
  saveWakeupConfig,
} = useChatroomBindings(chatroomId, () => room.value)

// ---- creator gate (R28.02 mirror; server is authoritative) ----------------

const session = useSessionStore()
const projectId = ref<string | undefined>(undefined)
watchEffect(async () => {
  const r = room.value
  if (r && !projectId.value) {
    try {
      projectId.value = (await getWorkspace(r.workspace_id)).project_id
    } catch {
      /* leave undefined — creator gate stays closed, which is safe */
    }
  }
})

const membersQuery = useQuery({
  queryKey: computed(() => tenancyKeys.projectMembers(projectId.value ?? '')),
  queryFn: () => projectsApi.listMembers(projectId.value!),
  enabled: computed(() => !!projectId.value && room.value?.created_by_user_id === null),
})

const isCreator = computed<boolean>(() => {
  const me = session.me
  const r = room.value
  if (!me || !r) return false
  if (me.is_admin) return true
  if (r.created_by_user_id !== null) return r.created_by_user_id === me.id
  const membership = membersQuery.data.value?.find((m) => m.user_id === me.id)
  return membership?.role === 'owner'
})

const roleOptions = computed(() => [
  { value: 'normal', label: t('conversation.observers.roleParticipant') },
  { value: 'observer', label: t('conversation.observers.roleObserver') },
])

// Show the guest-observer callout only when a guest link is open, observers
// exist, and disclosure is off (external guests observed without notice).
const hasObserver = computed(() => boundAgents.value.some((a) => a.role === 'observer'))
const showGuestObserverCallout = computed(
  () => flags.allow_guest_links && hasObserver.value && !flags.disclose_observers,
)

// ---- derived state --------------------------------------------------------

const breadcrumbs = computed(() => {
  const r = room.value
  return [
    {
      label: t('conversation.chatrooms.title'),
      ...(r && {
        to: { name: 'conversation.chatrooms', params: { workspaceId: r.workspace_id } },
      }),
    },
    {
      label: r?.name ?? '',
      ...(r && { to: { name: 'conversation.chatroom', params: { chatroomId } } }),
    },
  ]
})

const nameDirty = computed(
  () => name.value.trim().length > 0 && name.value.trim() !== (room.value?.name ?? ''),
)

const agentOptions = computed(() =>
  availableAgents.value.map((a) => ({ value: a.id, label: a.name })),
)

const hasNoAgents = computed(
  () =>
    !availableAgents.value.length &&
    !boundAgents.value.length &&
    !orphanAgentIds.value.length,
)

// ---- access toggles (optimistic immediate save per spec §4.2) -------------

function setFlag(key: keyof typeof flags, value: boolean): void {
  flags[key] = value
  void onSave()
}

// ---- guest link -----------------------------------------------------------

async function copyGuest(): Promise<void> {
  try {
    await navigator.clipboard?.writeText(guestUrl.value)
    toast.success(t('conversation.settings.linkCopied'))
  } catch {
    toast.error(t('conversation.settings.copyFailed'))
  }
}

// ---- danger zone: compact -------------------------------------------------

const compacting = ref(false)

async function onCompact(): Promise<void> {
  const ok = await confirm({
    title: t('conversation.settings.compactTitle'),
    message: t('conversation.settings.compactConfirm'),
    variant: 'warning',
    confirmLabel: t('conversation.settings.compact'),
  })
  if (!ok) return
  compacting.value = true
  try {
    await compactChatroom(chatroomId)
    toast.success(t('conversation.settings.compactRequested'))
  } catch {
    toast.error(t('conversation.settings.compactFailed'))
  } finally {
    compacting.value = false
  }
}

// ---- orchestrate load with bindings ---------------------------------------

async function loadRoom(): Promise<void> {
  await loadRoomBase()
  if (room.value) void loadBindings()
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

<template>
  <main class="p-6 settings">
    <SPageHeader
      :title="t('conversation.settings.title')"
      :breadcrumbs="breadcrumbs"
    >
      <template #prepend>
        <SButton
          variant="ghost"
          icon-only
          size="sm"
          :aria-label="t('conversation.settings.backToChatroom')"
          @click="router.push({ name: 'conversation.chatroom', params: { chatroomId } })"
        >
          <ArrowLeftIcon class="w-5 h-5" />
        </SButton>
      </template>
    </SPageHeader>

    <!-- Loading -->
    <div
      v-if="loading"
      class="settings__stack"
    >
      <SSkeleton
        v-for="n in 4"
        :key="n"
        variant="rect"
        height="140px"
      />
    </div>

    <!-- Load error -->
    <SAlert
      v-else-if="loadError || !room"
      variant="danger"
    >
      {{ t('conversation.settings.loadFailed') }}
      <template #actions>
        <SButton
          variant="ghost"
          size="sm"
          @click="loadRoom"
        >
          {{ t('conversation.settings.retry') }}
        </SButton>
      </template>
    </SAlert>

    <template v-else>
      <div class="settings__stack">
        <!-- General -->
        <SCard>
          <h2 class="settings__heading">
            {{ t('conversation.settings.general') }}
          </h2>
          <form @submit.prevent="onSave">
            <SFormField
              :label="t('conversation.settings.name')"
              name="chatroomName"
              required
            >
              <SInput
                v-model="name"
                :maxlength="INPUT_LIMITS.NAME"
                :disabled="saving"
              />
            </SFormField>
            <div class="settings__actions">
              <SButton
                type="submit"
                variant="primary"
                :loading="saving"
                :disabled="saving || !nameDirty"
              >
                {{ t('conversation.settings.saveChanges') }}
              </SButton>
            </div>
          </form>
          <SAlert
            v-if="saveError"
            variant="danger"
            focus-on-mount
            class="mt-2"
          >
            {{ t(saveError) }}
          </SAlert>
        </SCard>

        <!-- Access Control -->
        <SCard>
          <h2 class="settings__heading">
            {{ t('conversation.settings.access') }}
          </h2>

          <div
            class="access-row"
            :class="{ 'access-row--dimmed': flags.allow_project_owners_only }"
          >
            <div class="access-row__text">
              <p class="access-row__label">
                {{ t('conversation.settings.allowOrgMembers') }}
              </p>
              <p class="access-row__desc">
                {{ t('conversation.settings.allowOrgMembersDesc') }}
              </p>
            </div>
            <SToggle
              :model-value="flags.allow_org_members"
              :disabled="flags.allow_project_owners_only || saving"
              @update:model-value="(v) => setFlag('allow_org_members', v)"
            />
          </div>

          <div
            class="access-row"
            :class="{ 'access-row--dimmed': flags.allow_project_owners_only }"
          >
            <div class="access-row__text">
              <p class="access-row__label">
                {{ t('conversation.settings.allowProjectMembers') }}
              </p>
              <p class="access-row__desc">
                {{ t('conversation.settings.allowProjectMembersDesc') }}
              </p>
            </div>
            <SToggle
              :model-value="flags.allow_project_members"
              :disabled="flags.allow_project_owners_only || saving"
              @update:model-value="(v) => setFlag('allow_project_members', v)"
            />
          </div>

          <div class="access-row">
            <div class="access-row__text">
              <p class="access-row__label">
                {{ t('conversation.settings.allowProjectOwnersOnly') }}
              </p>
              <p class="access-row__desc">
                {{ t('conversation.settings.allowProjectOwnersOnlyDesc') }}
              </p>
            </div>
            <SToggle
              :model-value="flags.allow_project_owners_only"
              :disabled="saving"
              @update:model-value="(v) => setFlag('allow_project_owners_only', v)"
            />
          </div>

          <div class="access-row">
            <div class="access-row__text">
              <p class="access-row__label">
                {{ t('conversation.settings.allowGuestLinks') }}
              </p>
              <p class="access-row__desc">
                {{ t('conversation.settings.allowGuestLinksDesc') }}
              </p>
            </div>
            <SToggle
              :model-value="flags.allow_guest_links"
              :disabled="saving"
              @update:model-value="(v) => setFlag('allow_guest_links', v)"
            />
          </div>

          <!-- Observer disclosure: creator-only (R28.09). -->
          <div
            v-if="isCreator"
            class="access-row"
          >
            <div class="access-row__text">
              <p class="access-row__label">
                {{ t('conversation.observers.discloseLabel') }}
              </p>
              <p class="access-row__desc">
                {{ flags.disclose_observers
                  ? t('conversation.observers.discloseOnHelp')
                  : t('conversation.observers.discloseOffHelp') }}
              </p>
            </div>
            <SToggle
              :model-value="flags.disclose_observers"
              :disabled="saving"
              @update:model-value="(v) => saveDisclosure(v)"
            />
          </div>

          <SAlert
            v-if="showGuestObserverCallout"
            variant="warning"
            class="mt-2"
          >
            {{ t('conversation.observers.guestObserverCallout') }}
          </SAlert>
        </SCard>

        <!-- Guest Link -->
        <SCard v-if="flags.allow_guest_links">
          <h2 class="settings__heading">
            {{ t('conversation.settings.guestLinkLabel') }}
          </h2>
          <p class="access-row__desc mb-2">
            {{ t('conversation.settings.guestLinkHelp') }}
          </p>
          <div class="guest-link">
            <SInput
              :model-value="guestUrl"
              readonly
              class="guest-link__input"
            />
            <SButton
              variant="secondary"
              size="sm"
              @click="copyGuest"
            >
              <template #icon-left>
                <ClipboardDocumentIcon class="w-4 h-4" />
              </template>
              {{ t('conversation.settings.copy') }}
            </SButton>
          </div>
        </SCard>

        <!-- Bound Agents -->
        <SCard>
          <h2 class="settings__heading">
            {{ t('conversation.settings.agentBindings') }}
          </h2>

          <form
            class="agent-add"
            @submit.prevent="onAddAgent"
          >
            <SSelect
              v-model="selectedAgentId"
              :options="agentOptions"
              :placeholder="t('conversation.settings.selectAgent')"
              :disabled="bindingBusy || !agentOptions.length"
              class="agent-add__select"
            />
            <SSelect
              v-if="isCreator"
              v-model="selectedRole"
              :options="roleOptions"
              :disabled="bindingBusy"
              class="agent-add__role"
            />
            <SButton
              type="submit"
              variant="primary"
              :disabled="!selectedAgentId || bindingBusy"
            >
              {{ t('conversation.settings.add') }}
            </SButton>
          </form>

          <p
            v-if="hasNoAgents"
            class="access-row__desc mt-2"
          >
            {{ t('conversation.settings.noAgents') }}
          </p>

          <SAlert
            v-if="bindingError"
            variant="danger"
            focus-on-mount
            class="mt-2"
          >
            {{ t(bindingError) }}
          </SAlert>

          <div
            v-for="agent in boundAgents"
            :key="agent.id"
            class="agent-item"
          >
            <div class="agent-head">
              <p class="agent-head__name">
                {{ agent.name ?? agent.id.slice(0, 8) }}
              </p>
              <div class="agent-head__actions">
                <SSelect
                  v-if="isCreator"
                  :model-value="agent.role ?? 'normal'"
                  :options="roleOptions"
                  size="sm"
                  :disabled="bindingBusy"
                  @update:model-value="(v) => onSetRole(agent.id, v as 'normal' | 'observer')"
                />
                <SButton
                  variant="danger"
                  size="sm"
                  :disabled="bindingBusy"
                  @click="onRemoveAgent(agent.id)"
                >
                  {{ t('conversation.settings.removeAgent') }}
                </SButton>
              </div>
            </div>
            <p
              v-if="agent.role === 'observer'"
              class="access-row__desc agent-observer-note"
            >
              {{ t('conversation.observers.observerRoleHelp') }}
            </p>
            <SWakeupEditor
              :model-value="agent.wakeup_config"
              @update:model-value="(v) => saveWakeupConfig(agent.id, v)"
            />
            <DlqViewer :agent-id="agent.id" />
          </div>

          <!-- Orphan bindings whose agent no longer appears in the project. -->
          <div
            v-for="id in orphanAgentIds"
            :key="id"
            class="agent-item"
          >
            <div class="agent-head">
              <p class="agent-head__name agent-head__name--muted">
                {{ id.slice(0, 8) }} · {{ t('conversation.settings.unknownAgent') }}
              </p>
              <SButton
                variant="danger"
                size="sm"
                :disabled="bindingBusy"
                @click="onRemoveAgent(id)"
              >
                {{ t('conversation.settings.removeAgent') }}
              </SButton>
            </div>
          </div>
        </SCard>

        <!-- Concept Map (chatroom-owned; access inherited from the room) -->
        <template v-if="projectId">
          <SAlert variant="info">
            {{ t('conversation.conceptMap.chatroomInheritsAccess') }}
          </SAlert>
          <ConceptMapPanel
            :project-id="projectId"
            owner-kind="chatroom"
            :owner-id="chatroomId"
          />
        </template>

        <!-- Danger Zone -->
        <SCard class="danger-zone">
          <h2 class="settings__heading settings__heading--danger">
            {{ t('conversation.settings.dangerZone') }}
          </h2>

          <div class="danger-row">
            <div class="access-row__text">
              <p class="access-row__label">
                {{ t('conversation.settings.compactTitle') }}
              </p>
              <p class="access-row__desc">
                {{ t('conversation.settings.compactDesc') }}
              </p>
            </div>
            <SButton
              variant="secondary"
              :loading="compacting"
              :disabled="compacting"
              @click="onCompact"
            >
              <template #icon-left>
                <ArchiveBoxArrowDownIcon class="w-4 h-4" />
              </template>
              {{ t('conversation.settings.compact') }}
            </SButton>
          </div>

          <SDivider />

          <div class="danger-row">
            <div class="access-row__text">
              <p class="access-row__label">
                {{ t('conversation.settings.deleteConfirmTitle') }}
              </p>
              <p class="access-row__desc">
                {{ t('conversation.settings.deleteDesc') }}
              </p>
            </div>
            <SButton
              variant="danger"
              @click="onDelete"
            >
              <template #icon-left>
                <TrashIcon class="w-4 h-4" />
              </template>
              {{ t('conversation.settings.delete') }}
            </SButton>
          </div>
        </SCard>
      </div>
    </template>
  </main>
</template>

<style scoped>
.settings__stack {
  display: flex;
  flex-direction: column;
  gap: 24px;
  max-width: 640px;
  margin-top: 24px;
}

.settings__heading {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--color-fg);
  margin-bottom: 16px;
}

.settings__heading--danger {
  color: var(--color-danger);
}

.settings__actions {
  display: flex;
  justify-content: flex-end;
}

.access-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--color-border);
}

.access-row:last-child {
  border-bottom: none;
}

.access-row--dimmed {
  opacity: 0.5;
}

.access-row__text {
  min-width: 0;
}

.access-row__label {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-fg);
}

.access-row__desc {
  font-size: 0.75rem;
  color: var(--color-muted);
  margin-top: 2px;
}

.guest-link {
  display: flex;
  align-items: center;
  gap: 8px;
}

.guest-link__input {
  flex: 1;
}

.agent-add {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  margin-bottom: 12px;
}

.agent-add__select {
  flex: 1;
}

.agent-item {
  padding: 12px 0;
  border-top: 1px solid var(--color-border);
}

.agent-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.agent-head__name {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-fg);
}

.agent-head__name--muted {
  color: var(--color-muted);
}

.agent-head__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.agent-add__role {
  min-width: 130px;
}

.agent-observer-note {
  margin-bottom: 8px;
}

.danger-zone {
  border: 1px solid var(--color-danger);
}

.danger-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
}
</style>
