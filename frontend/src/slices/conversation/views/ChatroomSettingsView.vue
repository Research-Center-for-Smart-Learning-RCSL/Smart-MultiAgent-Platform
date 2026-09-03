<script setup lang="ts">
import { computed, onMounted, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowLeftIcon,
  ClipboardDocumentIcon,
  TrashIcon,
  ArchiveBoxArrowDownIcon,
  UserGroupIcon,
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
  SEmptyState,
  SLoadingSpinner,
  SWakeupEditor,
} from '@shared/ui'
import { useToast, useConfirmDialog } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { tenancyKeys, memberGroupsApi } from '@slices/tenancy'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import {
  compactChatroom,
  getGuestLink,
  getWorkspace,
  listChatrooms,
  listChatroomMemberGroups,
  setChatroomMemberGroups,
} from '../api'
import { DlqViewer } from '@slices/workflow'
import { ConceptMapPanel } from '@slices/agents'
import { useChatroomSettings } from '../composables/useChatroomSettings'
import { useChatroomBindings } from '../composables/useChatroomBindings'
import AgentActivityControl from '../components/AgentActivityControl.vue'
import AgentDraftAccess from '../components/AgentDraftAccess.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirmDialog()
const qc = useQueryClient()
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
  setFlag,
  saveDisclosure,
  saveDraftDisclosure,
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
  activityTypes,
  activityTypesFailed,
  loadBindings,
  onAddAgent,
  onRemoveAgent,
  onSetActivityControl,
  onSetDraftAccess,
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

// ---- member-group bindings (section 13.2a) --------------------------------
// Only fetched once the room actually has the tier on: a project that uses no
// groups should not pay two requests on every visit to this page.

const groupsQuery = useQuery({
  queryKey: computed(() => tenancyKeys.memberGroups(projectId.value ?? '')),
  queryFn: () => memberGroupsApi.list(projectId.value!),
  enabled: computed(() => !!projectId.value && flags.value.allow_member_groups),
})

// Named once so the query, the optimistic write and the invalidate cannot drift
// apart — the three together are what make F-2's fix hold.
const boundGroupsKey = ['conversation', 'chatroomMemberGroups', chatroomId] as const

const boundQuery = useQuery({
  // Not wrapped in `computed`: `chatroomId` is a plain string captured at setup,
  // so there is nothing here to react to and saying otherwise misleads.
  queryKey: boundGroupsKey,
  queryFn: () => listChatroomMemberGroups(chatroomId),
  enabled: computed(() => flags.value.allow_member_groups),
})

const availableGroups = computed(() => groupsQuery.data.value ?? [])
const boundGroupIds = computed(() => boundQuery.data.value ?? [])

/** SEC: the picker may only render once BOTH queries have actually answered.
 *
 *  `isLoading` is false on a *failed* query too, and `boundGroupIds` falls back
 *  to `[]` when there is no data — so gating on `!isLoading` alone drew the
 *  whole list with every box unchecked after a transient failure. `toggleGroup`
 *  then builds its payload from that empty set and the endpoint *replaces*, so
 *  one click silently deleted every real binding the room had, and the
 *  write-back at the end of `toggleGroup` recorded the truncated set as
 *  confirmed. A room access control must not be editable from a state that is
 *  merely the absence of an answer. */
const groupsReady = computed(() => groupsQuery.isSuccess.value && boundQuery.isSuccess.value)
const groupsFailed = computed(() => groupsQuery.isError.value || boundQuery.isError.value)
const savingGroups = ref(false)

/** Bumped whenever a toggle fails, and keyed into each checkbox so the input is
 *  re-created with the server's answer.
 *
 *  The checkbox is uncontrolled: the browser flips `el.checked` on click, and
 *  `:checked` only re-applies when the rendered value changes. After a failed
 *  save the confirmed set is by definition unchanged, so Vue patches nothing and
 *  the box keeps the click while the toast says it failed — a room access
 *  control showing a binding that does not exist. Changing the key forces the
 *  re-render that the unchanged value cannot. */
const pickerNonce = ref(0)

/** Re-read both group queries. Wired to the load-failure retry only. */
function reloadGroups(): void {
  void groupsQuery.refetch()
  void boundQuery.refetch()
}

/** Toggle one group in or out of the room's binding set.
 *
 *  Sends the whole resulting set, because the endpoint replaces rather than
 *  patches. That makes the read-back load-bearing: the next toggle computes its
 *  payload from `boundGroupIds`, so if the confirmed set is stale when the user
 *  clicks again, the replace silently drops the group the first click just added.
 *
 *  The server returns the applied set, so it is written straight into the cache —
 *  but that write has to survive the query's own refetches (F-2). `boundQuery`
 *  overrides nothing, so it is always stale and always focus-refetchable, and
 *  query-core writes a resolving fetch's data unconditionally, with no regard for
 *  a manual write that landed meanwhile. A read already in flight therefore
 *  overwrote the applied set: the box flipped back under a success toast, and
 *  because the endpoint replaces, the next click rebuilt its payload from the
 *  reverted set and deleted the binding the first click had added.
 *
 *  The `invalidateQueries` is what closes it, and it closes it twice over:
 *  it defaults `cancelRefetch: true`, so it aborts a read still in flight
 *  *and* re-reads, which makes the server the last writer whatever the ordering.
 *
 *  A `cancelQueries` before the PUT was tried here and removed as dead weight.
 *  Every exit from this function already cancels: the invalidate above on the
 *  success path, and `boundQuery.refetch()` — which also defaults
 *  `cancelRefetch: true` — on the failure path. It could only ever have
 *  suppressed a sub-frame flicker while `savingGroups` had the control disabled,
 *  and no test could be written that failed without it. */
async function toggleGroup(groupId: string): Promise<void> {
  // Belt to the template's braces: the picker is not rendered unless both
  // queries answered, and a payload built from an unanswered set would replace
  // the room's real bindings with it.
  if (savingGroups.value || !groupsReady.value) return
  const current = boundGroupIds.value
  const next = current.includes(groupId)
    ? current.filter((id) => id !== groupId)
    : [...current, groupId]
  savingGroups.value = true
  try {
    const applied = await setChatroomMemberGroups(chatroomId, next)
    qc.setQueryData(boundGroupsKey, applied)
    void qc.invalidateQueries({ queryKey: boundGroupsKey })
    toast.success(t('conversation.settings.boundGroupsSaved', { count: applied.length }))
  } catch {
    // Re-read the server's answer, then force the picker to re-render onto it:
    // the refetched set is usually identical, and an identical value patches no
    // DOM (see `pickerNonce`), leaving the box on the click that failed.
    await boundQuery.refetch()
    pickerNonce.value += 1
    toast.error(t('conversation.settings.boundGroupsFailed'))
  } finally {
    savingGroups.value = false
  }
}

// F-3, same substitution as `useObservations`: for a NULL-creator room the
// server falls back to moderator semantics, which an inherited ORG_OWNER
// satisfies with no `project_members` row — so scanning that table answered a
// question the server had already answered, and answered it wrong.
const isCreator = computed<boolean>(() => {
  const me = session.me
  const r = room.value
  if (!me || !r) return false
  if (me.is_admin) return true
  if (r.created_by_user_id !== null) return r.created_by_user_id === me.id
  return r.is_moderator === true
})

const roleOptions = computed(() => [
  { value: 'normal', label: t('conversation.observers.roleParticipant') },
  { value: 'observer', label: t('conversation.observers.roleObserver') },
])

// F-2: guest links plus an observer is the whole condition. The disclosure term
// used to be here and had it backwards — `_to_out` forces BOTH
// `disclose_observers` and `observers_present` to false for every pure guest
// whatever the room setting says (a deliberate suppression, R28.02), so a guest
// is never shown the indicator in either state. Keying off the flag therefore
// stayed silent in the configuration a teacher most needs warned about: guest
// links open with disclosure left at its default, which is on.
const hasObserver = computed(() => boundAgents.value.some((a) => a.role === 'observer'))
const showGuestObserverCallout = computed(
  () => flags.value.allow_guest_links && hasObserver.value,
)
// [R32.03]. Read from the bindings listing rather than from the room DTO's
// `drafts_readable`: that field folds the disclosure flag in, so it is false in
// exactly the state this callout exists to warn about.
const anyAgentReadsDrafts = computed(() =>
  boundAgents.value.some((a) => a.may_read_drafts === true),
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
  if (flags.value.allow_guest_links && room.value) {
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
  <div class="settings">
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

          <!--
            Section 13.2a. Mutually exclusive with allow_project_members
            (R13.04): `setFlag` moves both in one patch, so turning this on
            visibly clears the row above rather than producing a 422.
          -->
          <div
            class="access-row"
            :class="{ 'access-row--dimmed': flags.allow_project_owners_only }"
          >
            <div class="access-row__text">
              <p class="access-row__label">
                {{ t('conversation.settings.allowMemberGroups') }}
              </p>
              <p class="access-row__desc">
                {{ t('conversation.settings.allowMemberGroupsDesc') }}
              </p>
            </div>
            <SToggle
              :model-value="flags.allow_member_groups"
              :disabled="flags.allow_project_owners_only || saving"
              @update:model-value="(v) => setFlag('allow_member_groups', v)"
            />
          </div>

          <!-- Which groups. Only meaningful while the tier above is on. -->
          <div
            v-if="flags.allow_member_groups && !flags.allow_project_owners_only"
            class="access-row access-row--stacked"
          >
            <div class="access-row__text">
              <p class="access-row__label">
                {{ t('conversation.settings.boundGroups') }}
              </p>
              <p class="access-row__desc">
                {{ t('conversation.settings.boundGroupsDesc') }}
              </p>
            </div>
            <SAlert
              v-if="groupsFailed"
              variant="danger"
            >
              {{ t('conversation.settings.boundGroupsLoadFailed') }}
              <template #actions>
                <button
                  class="underline"
                  @click="reloadGroups()"
                >
                  {{ t('conversation.settings.boundGroupsRetry') }}
                </button>
              </template>
            </SAlert>
            <SLoadingSpinner v-else-if="!groupsReady" />
            <SEmptyState
              v-else-if="!availableGroups.length"
              :icon="UserGroupIcon"
              :title="t('conversation.settings.noGroupsYet')"
            />
            <ul
              v-else
              class="group-picker"
            >
              <li
                v-for="g in availableGroups"
                :key="`${g.id}:${pickerNonce}`"
              >
                <label>
                  <input
                    type="checkbox"
                    :checked="boundGroupIds.includes(g.id)"
                    :disabled="savingGroups"
                    @change="toggleGroup(g.id)"
                  >
                  <span>{{ g.name }}</span>
                </label>
              </li>
            </ul>
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

          <!-- Draft disclosure: creator-only ([R32.05]), beside its observer
               sibling because they are the same kind of decision. It is shown
               whether or not any agent currently holds the grant: a creator
               deciding the room's posture in advance is exactly the moment to
               make it, and hiding the switch until a grant exists would mean
               the first grant always lands with disclosure at its default. -->
          <div
            v-if="isCreator"
            class="access-row"
          >
            <div class="access-row__text">
              <p class="access-row__label">
                {{ t('conversation.drafts.discloseLabel') }}
              </p>
              <p class="access-row__desc">
                {{ flags.disclose_drafts
                  ? t('conversation.drafts.discloseOnHelp')
                  : t('conversation.drafts.discloseOffHelp') }}
              </p>
            </div>
            <SToggle
              :model-value="flags.disclose_drafts"
              :disabled="saving"
              @update:model-value="(v) => saveDraftDisclosure(v)"
            />
          </div>

          <!-- Not a hypothetical: turning this off produces a room where a
               person's unsent words are read and nobody is told. §10 records it
               as an accepted, unmitigated risk, so the one thing the product can
               do is say so at the moment of the choice. -->
          <SAlert
            v-if="isCreator && !flags.disclose_drafts && anyAgentReadsDrafts"
            variant="warning"
            class="mt-2"
          >
            {{ t('conversation.drafts.undisclosedCallout') }}
          </SAlert>

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
            <!-- Delegated activity control ([R30.37]). Creator-only, like the role
                 picker above and for the same reason: it is an authority decision
                 about this room, and a non-creator is not even told the layout. -->
            <AgentActivityControl
              v-if="isCreator"
              :agent="agent"
              :activity-types="activityTypes"
              :activity-types-failed="activityTypesFailed"
              :busy="bindingBusy"
              @save="(granted, typeIds) => onSetActivityControl(agent.id, granted, typeIds)"
            />
            <!-- Live draft reading ([R32.03]). Creator-only on the same terms,
                 and a separate control from the one above because it is a
                 separate authority written by a separate route. -->
            <AgentDraftAccess
              v-if="isCreator"
              :agent="agent"
              :busy="bindingBusy"
              :disclosed="flags.disclose_drafts"
              @save="(granted) => onSetDraftAccess(agent.id, granted)"
            />
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
  </div>
</template>

<style scoped>
.settings__stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: 640px;
  margin-top: var(--space-6);
}

.settings__heading {
  font-size: var(--font-size-lg);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin-bottom: var(--space-4);
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
  gap: var(--space-4);
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--color-border-subtle);
}

.access-row:last-child {
  border-bottom: none;
}

.access-row--dimmed {
  opacity: 0.5;
}

/* The group picker is a list, not a control sitting opposite a label, so it
   stacks under its description instead of inheriting the row's space-between. */
.access-row--stacked {
  display: block;
}

.group-picker {
  display: flex;
  flex-direction: column;
  gap: var(--space-1-5);
  margin-top: var(--space-2);
  list-style: none;
  padding: 0;
}

.group-picker label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-sm);
  color: var(--color-fg);
  cursor: pointer;
}

.access-row__text {
  min-width: 0;
}

.access-row__label {
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
}

.access-row__desc {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  margin-top: var(--space-0-5);
}

.guest-link {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.guest-link__input {
  flex: 1;
}

.agent-add {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.agent-add__select {
  flex: 1;
}

.agent-item {
  padding: var(--space-3) 0;
  border-top: 1px solid var(--color-border-subtle);
}

.agent-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.agent-head__name {
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
}

.agent-head__name--muted {
  color: var(--color-muted);
}

.agent-head__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.agent-add__role {
  min-width: 130px;
}

.agent-observer-note {
  margin-bottom: var(--space-2);
}

.danger-zone {
  border: 1px solid var(--color-danger);
}

.danger-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) 0;
}
</style>
