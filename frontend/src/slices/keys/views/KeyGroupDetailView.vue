<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  Bars2Icon,
  ChevronDownIcon,
  ChevronUpIcon,
  XMarkIcon,
  PencilIcon,
  CheckIcon,
  PlusIcon,
  TrashIcon,
  UsersIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SBadge,
  SButton,
  SInput,
  SSelect,
  SEmptyState,
  SAlert,
} from '@shared/ui'
import { useInlineRename, useConfirmDialog, useToast } from '@shared/composables'
import { useKeyGroupDetail } from '../composables/useKeyGroups'
import { useProjectKeys } from '../composables/useProjectKeys'
import type { MemberPatch } from '../api/key-groups'
import { CAPABILITIES } from '../api/keys'
import type { ApiKeyProvider } from '../api/keys'
import CapabilityChip from '../components/CapabilityChip.vue'
import MemberConfigPanel from '../components/MemberConfigPanel.vue'

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()
const route = useRoute()
const router = useRouter()
const projectId = computed(() => route.params.projectId as string)
const groupId = computed(() => route.params.id as string)

const {
  detail, error, reload,
  rename: renameGroup, remove: removeGroup,
  addMember, removeMember, patchMember, reorder,
} = useKeyGroupDetail(() => groupId.value)
const { carried, reload: reloadCarried } = useProjectKeys(() => projectId.value)

const selectedKeyId = ref('')
const expandedMemberId = ref<string | null>(null)
const draggingId = ref<string | null>(null)
const dropTargetId = ref<string | null>(null)
const savingMemberId = ref<string | null>(null)

const rename = useInlineRename({
  current: () => detail.value?.group.name ?? '',
  save: async (name) => {
    try {
      await renameGroup(name)
    } catch (e) {
      toast.error(t('keys.groups.renameError'))
      throw e
    }
  },
})

const breadcrumbs = computed(() => [
  { label: t('keys.groups.listTitle'), to: { name: 'keys.groupList', params: { projectId: projectId.value } } },
  { label: detail.value?.group.name ?? '' },
])

const carriedKeyMap = computed(() => {
  const map = new Map<string, (typeof carried.value)[number]>()
  for (const k of carried.value) map.set(k.id, k)
  return map
})

const availableKeys = computed(() => {
  if (!detail.value) return []
  const memberIds = new Set(detail.value.members.map((m) => m.key_id))
  return carried.value
    .filter((k) => CAPABILITIES[k.provider as ApiKeyProvider]?.includes('llm_chat'))
    .map((k) => ({
      value: k.id,
      label: `${k.provider} - ${k.name}`,
      disabled: memberIds.has(k.id),
    }))
})

function onDragStart(e: DragEvent, keyId: string) {
  draggingId.value = keyId
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', keyId)
  }
}

function onDragOver(e: DragEvent, keyId: string) {
  e.preventDefault()
  dropTargetId.value = keyId
}

function onDragLeave() {
  dropTargetId.value = null
}

function onDrop(targetKeyId: string) {
  const src = draggingId.value
  draggingId.value = null
  dropTargetId.value = null
  if (!src || src === targetKeyId || !detail.value) return
  const order = detail.value.members.map((m) => m.key_id)
  const from = order.indexOf(src)
  const to = order.indexOf(targetKeyId)
  if (from < 0 || to < 0) return
  order.splice(to, 0, ...order.splice(from, 1))
  const priorities: Record<string, number> = {}
  order.forEach((kid, i) => {
    priorities[kid] = i + 1
  })
  reorder(priorities)
}

function onDragEnd() {
  draggingId.value = null
  dropTargetId.value = null
}

function toggleExpand(keyId: string) {
  expandedMemberId.value = expandedMemberId.value === keyId ? null : keyId
}

async function onSaveMemberConfig(keyId: string, patch: MemberPatch) {
  savingMemberId.value = keyId
  try {
    await patchMember(keyId, patch)
    toast.success(t('keys.groups.memberUpdated'))
    expandedMemberId.value = null
  } catch {
    toast.error(t('keys.groups.memberUpdateFailed'))
  } finally {
    savingMemberId.value = null
  }
}

async function onRemoveMember(keyId: string) {
  const key = carriedKeyMap.value.get(keyId)
  const ok = await confirm({
    title: t('keys.groups.removeMemberTitle'),
    message: t('keys.groups.removeMemberBody', { name: key?.name ?? keyId }),
    confirmLabel: t('keys.groups.remove'),
    variant: 'warning',
  })
  if (!ok) return
  try {
    await removeMember(keyId)
    if (expandedMemberId.value === keyId) expandedMemberId.value = null
  } catch {
    toast.error(t('keys.groups.removeMemberFailed'))
  }
}

async function onAddMember() {
  if (!selectedKeyId.value) return
  try {
    await addMember(selectedKeyId.value)
    selectedKeyId.value = ''
  } catch {
    toast.error(t('keys.groups.addMemberFailed'))
  }
}

async function onDeleteGroup() {
  const ok = await confirm({
    title: t('keys.groups.deleteTitle'),
    message: t('keys.groups.deleteBody', { name: detail.value?.group.name ?? '' }),
    confirmLabel: t('keys.groups.delete'),
    variant: 'error',
  })
  if (!ok) return
  try {
    await removeGroup()
    await router.replace({ name: 'keys.groupList', params: { projectId: projectId.value } })
  } catch {
    toast.error(t('keys.groups.deleteFailed'))
  }
}

</script>

<template>
  <main class="p-6">
    <SPageHeader :breadcrumbs="breadcrumbs">
      <template #default>
        <div class="flex items-center gap-2">
          <template v-if="!rename.renaming.value">
            <h1 class="text-2xl font-semibold">
              {{ detail?.group.name ?? '' }}
            </h1>
            <SButton
              v-if="detail"
              variant="ghost"
              icon-only
              size="sm"
              :aria-label="$t('keys.groups.rename')"
              @click="rename.start"
            >
              <PencilIcon class="w-4 h-4 text-[var(--color-muted)]" />
            </SButton>
          </template>
          <template v-else>
            <SInput
              v-model="rename.nameDraft.value"
              class="max-w-[400px]"
              size="sm"
              @keydown.enter="rename.save"
              @keydown.escape="rename.cancel"
            />
            <SButton
              variant="ghost"
              icon-only
              size="sm"
              @click="rename.save"
            >
              <CheckIcon class="w-4 h-4" />
            </SButton>
            <SButton
              variant="ghost"
              icon-only
              size="sm"
              @click="rename.cancel"
            >
              <XMarkIcon class="w-4 h-4" />
            </SButton>
          </template>
        </div>
      </template>
      <template #actions>
        <SButton
          variant="danger"
          @click="onDeleteGroup"
        >
          <template #icon-left>
            <TrashIcon class="w-4 h-4" />
          </template>
          {{ $t('keys.groups.deleteGroup') }}
        </SButton>
      </template>
    </SPageHeader>

    <SAlert
      v-if="error"
      variant="danger"
      class="mt-4"
    >
      {{ error }}
    </SAlert>

    <section
      v-if="detail"
      class="mt-6"
    >
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold">
          {{ $t('keys.groups.members') }} ({{ detail.members.length }})
        </h2>
        <div class="flex items-center gap-2">
          <SSelect
            v-model="selectedKeyId"
            :options="availableKeys"
            :placeholder="$t('keys.groups.addMember')"
            size="sm"
            :aria-label="$t('keys.groups.addMember')"
            data-testid="add-member-select"
          />
          <SButton
            variant="primary"
            size="sm"
            :disabled="!selectedKeyId"
            data-testid="add-member"
            @click="onAddMember"
          >
            <template #icon-left>
              <PlusIcon class="w-4 h-4" />
            </template>
            {{ $t('keys.groups.add') }}
          </SButton>
        </div>
      </div>

      <SEmptyState
        v-if="detail.members.length === 0"
        :icon="UsersIcon"
        :title="$t('keys.groups.noMembers')"
        :text="$t('keys.groups.noMembersDescription')"
      >
        <template #action>
          <SSelect
            v-model="selectedKeyId"
            :options="availableKeys"
            :placeholder="$t('keys.groups.addMember')"
            size="sm"
          />
        </template>
      </SEmptyState>

      <div
        role="list"
        class="flex flex-col gap-2"
      >
        <div
          v-for="m in detail.members"
          :key="m.key_id"
          :data-testid="`member-${m.key_id}`"
        >
          <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -->
          <div
            role="listitem"
            :class="[
              'flex items-center gap-3 px-4 py-3 border rounded-[var(--radius-md)] bg-[var(--color-bg)] transition-shadow',
              draggingId === m.key_id ? 'shadow-md opacity-90 border-[var(--color-accent)]' : 'border-[var(--color-border)]',
              dropTargetId === m.key_id && draggingId !== m.key_id ? 'border-t-2 border-t-[var(--color-accent)]' : '',
              expandedMemberId === m.key_id ? 'rounded-b-none' : '',
            ]"
            @dragover="onDragOver($event, m.key_id)"
            @dragleave="onDragLeave"
            @drop.prevent="onDrop(m.key_id)"
          >
            <button
              type="button"
              draggable="true"
              class="cursor-grab active:cursor-grabbing text-[var(--color-muted)] p-0 bg-transparent border-0"
              :aria-label="$t('keys.groups.reorderHandle')"
              @dragstart="onDragStart($event, m.key_id)"
              @dragend="onDragEnd"
            >
              <Bars2Icon class="w-5 h-5" />
            </button>

            <SBadge variant="info">
              #{{ m.priority }}
            </SBadge>

            <CapabilityChip
              v-if="carriedKeyMap.get(m.key_id)"
              :provider="carriedKeyMap.get(m.key_id)!.provider"
            />
            <span class="text-sm truncate max-w-[30ch]">
              {{ carriedKeyMap.get(m.key_id)?.name ?? m.key_id }}
            </span>
            <code class="text-xs font-mono text-[var(--color-muted)]">
              {{ carriedKeyMap.get(m.key_id)?.masked_preview ?? '' }}
            </code>

            <div class="ml-auto flex items-center gap-1">
              <SButton
                variant="ghost"
                icon-only
                size="sm"
                @click="toggleExpand(m.key_id)"
              >
                <component
                  :is="expandedMemberId === m.key_id ? ChevronUpIcon : ChevronDownIcon"
                  class="w-4 h-4"
                />
              </SButton>

              <SButton
                variant="ghost"
                icon-only
                size="sm"
                class="hover:text-[var(--color-danger)]"
                data-testid="member-remove"
                @click="onRemoveMember(m.key_id)"
              >
                <XMarkIcon class="w-4 h-4" />
              </SButton>
            </div>
          </div>

          <MemberConfigPanel
            v-if="expandedMemberId === m.key_id"
            :member="m"
            :saving="savingMemberId === m.key_id"
            @save="onSaveMemberConfig(m.key_id, $event)"
          />
        </div>
      </div>
    </section>
  </main>
</template>
