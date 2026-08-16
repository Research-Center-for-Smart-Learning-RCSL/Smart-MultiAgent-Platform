<script setup lang="ts">
// Installing a shipped agent pack into this project ([R30.35]).
//
// Copy-on-import, not an opt-in like the activity examples: an agent needs a
// project-owned key group carrying a real provider key, so a pack cannot be a
// shared platform row. That is why this dialog asks for a key group at all.
//
// Before installing it shows each agent's *preferred* provider and the activity
// types it is written for. The preference, not the resolved value: which
// provider an agent ends up on is decided server-side against the chosen key
// group, and no endpoint answers that question ahead of the install. The
// resolved provider is reported afterwards, from the install report.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import {
  EyeSlashIcon,
  InformationCircleIcon,
  PencilSquareIcon,
  PuzzlePieceIcon,
  UserGroupIcon,
} from '@heroicons/vue/24/outline'

import {
  SModal,
  SButton,
  SBadge,
  SSelect,
  SFormField,
  SEmptyState,
  SQueryError,
  SLoadingSpinner,
} from '@shared/ui'
import { useToast } from '@shared/composables'
import { keyGroupsApi, keysKeys, type KeyGroup } from '@slices/keys'

import { agentsApi } from '../api'
import { agentKeys } from '../queries'

const props = defineProps<{ projectId: string; open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'installed'): void }>()

const { t } = useI18n()
const qc = useQueryClient()
const toast = useToast()

/** Which pack is mid-request, so only its own button shows a pending state. */
const pendingPack = ref<string | null>(null)
// `string | number` because that is what SSelect emits; the options only ever
// carry key-group uuids, so the String() at the call site narrows rather than
// converts.
const keyGroupId = ref<string | number>('')

const packsQuery = useQuery({
  queryKey: agentKeys.examplePacks(props.projectId),
  queryFn: () => agentsApi.listExamplePacks(props.projectId),
  // Only while the dialog is open: this is a capability-gated call with no
  // reason to run on every visit to the agents page.
  enabled: computed(() => props.open),
})

const keyGroupsQuery = useQuery({
  queryKey: keysKeys.keyGroups(props.projectId),
  queryFn: () => keyGroupsApi.listForProject(props.projectId),
  enabled: computed(() => props.open),
})

const packs = computed(() => packsQuery.data.value ?? [])
const keyGroups = computed<KeyGroup[]>(() => keyGroupsQuery.data.value ?? [])
const isLoading = computed(() => packsQuery.isLoading.value)
const isError = computed(() => packsQuery.isError.value)

const keyGroupOptions = computed(() =>
  keyGroups.value.map((g) => ({ value: g.id, label: g.name })),
)

/** Why the install buttons are inert, when they are. Without this the dialog
 *  renders fully, offers a select with nothing in it, and leaves the reader to
 *  guess whether the project has no key groups or the request for them failed. */
const keyGroupProblem = computed<string | null>(() => {
  if (keyGroupsQuery.isLoading.value) return null
  if (keyGroupsQuery.isError.value) return t('agents.examplePacks.keyGroupsFailed')
  if (keyGroups.value.length === 0) return t('agents.examplePacks.noKeyGroups')
  return null
})

// Preselect the only choice rather than making the installer open a select with
// one entry. Deliberately not a default when there are several: which key group
// an agent runs on is a real decision.
watch(
  keyGroups,
  (groups) => {
    if (!keyGroupId.value && groups.length === 1) keyGroupId.value = groups[0]!.id
  },
  { immediate: true },
)

const canInstall = computed(() => keyGroupId.value !== '')

/** Any pack carrying an agent meant to observe silently. Shown before anything
 *  is installed, because an observer reads the room without appearing in it and
 *  that is a property an installer should know up front, not discover. */
const anyObserver = computed(() =>
  packs.value.some((p) => p.agents.some((a) => a.room_role === 'observer')),
)

/** Any pack carrying a design agent -- `room_role: null` is exactly and only
 *  that. Shown up front because "design agent" reads as something that
 *  configures agents for you, and it does not: it drafts material for the
 *  teacher's own room, and applying a draft is a manual copy and paste. */
const anyDesignAgent = computed(() =>
  packs.value.some((p) => p.agents.some((a) => a.room_role === null)),
)

const installMutation = useMutation({
  mutationFn: (packKey: string) =>
    agentsApi.installExamplePack(props.projectId, packKey, {
      key_group_id: String(keyGroupId.value),
      model_hint: null,
    }),
  onSuccess: (report) => {
    // Read the group name off the still-current list before invalidating it --
    // the report carries `group_id` but no name, and only the catalogue has one.
    const groupName = packs.value.find((p) => p.pack_key === report.pack_key)?.group_name ?? ''

    void qc.invalidateQueries({ queryKey: agentKeys.examplePacks(props.projectId) })
    void qc.invalidateQueries({ queryKey: agentKeys.agents(props.projectId) })
    emit('installed')
    if (report.created.length > 0) {
      // A set, not one value: agents can resolve to different providers when
      // their preferred hints differ and the key group serves only some.
      const providers = [...new Set(report.created.map((a) => a.model_hint))].join(', ')
      toast.success(
        t('agents.examplePacks.installed', { count: report.created.length, providers }),
      )
    } else if (!report.group_created) {
      // Only when the run really created nothing. A run that created a group is
      // not a no-op, and saying so was F-8's whole symptom.
      toast.info(t('agents.examplePacks.nothingToInstall'))
    }
    if (report.group_created) {
      toast.success(t('agents.examplePacks.groupCreated', { group: groupName }))
    }
  },
  onError: () => {
    toast.error(t('agents.examplePacks.installFailed'))
  },
  onSettled: () => {
    pendingPack.value = null
  },
})

function install(packKey: string): void {
  pendingPack.value = packKey
  installMutation.mutate(packKey)
}

function roleLabel(role: string | null): string {
  if (role === 'observer') return t('agents.examplePacks.roleObserver')
  if (role === 'normal') return t('agents.examplePacks.roleNormal')
  return t('agents.examplePacks.roleDesign')
}
</script>

<template>
  <SModal
    :open="open"
    :title="t('agents.examplePacks.title')"
    size="lg"
    @close="emit('close')"
  >
    <div class="flex flex-col gap-4">
      <p class="text-sm text-[var(--color-muted)]">
        {{ t('agents.examplePacks.intro') }}
      </p>

      <SQueryError
        v-if="isError"
        :message="t('agents.examplePacks.errorText')"
        :retry-label="t('agents.examplePacks.retry')"
        @retry="packsQuery.refetch()"
      />

      <div
        v-else-if="isLoading"
        class="flex justify-center py-8"
      >
        <SLoadingSpinner />
      </div>

      <SEmptyState
        v-else-if="packs.length === 0"
        :icon="PuzzlePieceIcon"
        :title="t('agents.examplePacks.emptyTitle')"
        :text="t('agents.examplePacks.emptyText')"
      />

      <template v-else>
        <div
          class="flex gap-2 rounded-md border border-[var(--color-border)] p-3"
          role="note"
        >
          <InformationCircleIcon class="w-5 h-5 shrink-0 text-[var(--color-muted)]" />
          <p class="text-sm text-[var(--color-fg)]">
            {{ t('agents.examplePacks.installScopeNotice') }}
          </p>
        </div>

        <div
          v-if="anyObserver"
          class="flex gap-2 rounded-md border border-[var(--color-border)] p-3"
          role="note"
        >
          <EyeSlashIcon class="w-5 h-5 shrink-0 text-[var(--color-muted)]" />
          <p class="text-sm text-[var(--color-fg)]">
            {{ t('agents.examplePacks.observerNotice') }}
          </p>
        </div>

        <div
          v-if="anyDesignAgent"
          class="flex gap-2 rounded-md border border-[var(--color-border)] p-3"
          role="note"
        >
          <PencilSquareIcon class="w-5 h-5 shrink-0 text-[var(--color-muted)]" />
          <p class="text-sm text-[var(--color-fg)]">
            {{ t('agents.examplePacks.designNotice') }}
          </p>
        </div>

        <SFormField
          name="pack-key-group"
          :label="t('agents.examplePacks.keyGroup')"
          :help="t('agents.examplePacks.keyGroupHelp')"
        >
          <SSelect
            v-model="keyGroupId"
            :options="keyGroupOptions"
            :placeholder="t('agents.examplePacks.keyGroupPlaceholder')"
          />
        </SFormField>
        <!-- Its own line rather than SFormField's `error` slot: this is a
             precondition the reader has not violated, not a validation failure
             on something they typed. -->
        <p
          v-if="keyGroupProblem"
          class="text-sm text-[var(--color-danger)]"
          role="alert"
        >
          {{ keyGroupProblem }}
        </p>

        <ul class="flex flex-col gap-3">
          <li
            v-for="pack in packs"
            :key="pack.pack_key"
            class="flex flex-col gap-2 rounded-md border border-[var(--color-border)] p-3"
            :data-testid="`pack-${pack.pack_key}`"
          >
            <div class="flex items-start justify-between gap-4">
              <div class="flex flex-col gap-1">
                <div class="flex items-center gap-2">
                  <UserGroupIcon class="w-4 h-4 text-[var(--color-muted)]" />
                  <span class="font-medium">{{ pack.title }}</span>
                  <SBadge
                    v-if="pack.fully_installed"
                    size="sm"
                    variant="success"
                  >
                    {{ t('agents.examplePacks.installedBadge') }}
                  </SBadge>
                </div>
                <span class="font-mono text-xs text-[var(--color-muted)]">{{ pack.pack_key }}</span>
                <span class="text-xs text-[var(--color-muted)]">
                  {{ t('agents.examplePacks.forCourse', { course: pack.for_course }) }}
                </span>
              </div>

              <!-- Disabled while *any* install is in flight, not just this row's.
                   `pendingPack` is single-valued, so a second install started
                   before the first settles overwrites it and the first one's
                   completion clears the pending state for the wrong pack. -->
              <SButton
                variant="primary"
                size="sm"
                :disabled="!canInstall || pendingPack !== null"
                :data-testid="`install-${pack.pack_key}`"
                @click="install(pack.pack_key)"
              >
                {{ t('agents.examplePacks.install') }}
              </SButton>
            </div>

            <ul class="flex flex-col gap-1 pl-6">
              <li
                v-for="agent in pack.agents"
                :key="agent.key"
                class="flex flex-wrap items-center gap-2 text-sm"
              >
                <span>{{ agent.name }}</span>
                <SBadge
                  size="sm"
                  variant="neutral"
                >
                  {{ roleLabel(agent.room_role) }}
                </SBadge>
                <SBadge
                  size="sm"
                  variant="neutral"
                >
                  {{
                    t('agents.examplePacks.prefersProvider', {
                      provider: agent.preferred_model_hint,
                    })
                  }}
                </SBadge>
                <SBadge
                  v-if="agent.installed"
                  size="sm"
                  variant="success"
                >
                  {{ t('agents.examplePacks.agentInstalled') }}
                </SBadge>
                <span
                  v-if="agent.binds_activity_types.length > 0"
                  class="basis-full text-xs text-[var(--color-muted)]"
                >
                  {{
                    t('agents.examplePacks.bindsActivities', {
                      types: agent.binds_activity_types.join(', '),
                    })
                  }}
                </span>
              </li>
            </ul>
          </li>
        </ul>

        <p class="text-xs text-[var(--color-muted)]">
          {{ t('agents.examplePacks.nextSteps') }}
        </p>
      </template>
    </div>

    <template #footer>
      <SButton
        variant="secondary"
        @click="emit('close')"
      >
        {{ t('agents.examplePacks.close') }}
      </SButton>
    </template>
  </SModal>
</template>
