<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
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
  SFormField,
  SSelect,
  SToggle,
  SDivider,
  SEmptyState,
  SAlert,
} from '@shared/ui'
import { useInlineRename, useConfirmDialog, useToast } from '@shared/composables'
import { useKeyGroupDetail } from '../composables/useKeyGroups'
import { useProjectKeys } from '../composables/useProjectKeys'
import { keyGroupsApi, type MemberPatch } from '../api/key-groups'
import CapabilityChip from '../components/CapabilityChip.vue'
import type { ApiKey } from '../api/keys'

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()
const route = useRoute()
const router = useRouter()
const projectId = computed(() => route.params.projectId as string)
const groupId = computed(() => route.params.id as string)

const { detail, error, reload, addMember, removeMember, patchMember, reorder } =
  useKeyGroupDetail(() => groupId.value)
const { carried, reload: reloadCarried } = useProjectKeys(() => projectId.value)

const selectedKeyId = ref('')
const expandedMemberId = ref<string | null>(null)
const draggingId = ref<string | null>(null)
const dropTargetId = ref<string | null>(null)

const rename = useInlineRename({
  current: () => detail.value?.group.name ?? '',
  save: async (name) => {
    try {
      await keyGroupsApi.rename(groupId.value, name)
    } catch (e) {
      toast.error(t('keys.groups.renameError'))
      throw e
    }
    void reload().catch(() => {})
  },
})

const breadcrumbs = computed(() => [
  { label: t('keys.groups.listTitle'), to: { name: 'keys.groupList', params: { projectId: projectId.value } } },
  { label: detail.value?.group.name ?? '' },
])

const availableKeys = computed(() => {
  if (!detail.value) return []
  const memberIds = new Set(detail.value.members.map((m) => m.key_id))
  return carried.value.map((k) => ({
    value: k.id,
    label: `${k.provider} - ${k.name}`,
    disabled: memberIds.has(k.id),
  }))
})

function carriedKeyFor(keyId: string): ApiKey | undefined {
  return carried.value.find((k) => k.id === keyId)
}

// Drag-reorder
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

// Member config forms
interface MemberForm {
  rotate_on_error_codes: number[]
  rotate_on_token_quota: boolean
  retry_on_error: boolean
  retry_initial_delay_ms: number
  retry_multiplier: number
  retry_max_delay_ms: number
  retry_max: number
  retry_jitter_pct: number
  max_input_tokens_per_hour: number | null
  max_output_tokens_per_hour: number | null
  max_requests_per_hour: number | null
}

const memberForms = reactive<Record<string, MemberForm>>({})
const newErrorCode = ref<Record<string, string>>({})
const savingMemberId = ref<string | null>(null)

function ensureForm(keyId: string): MemberForm {
  if (!memberForms[keyId] && detail.value) {
    const m = detail.value.members.find((x) => x.key_id === keyId)
    if (m) {
      memberForms[keyId] = {
        rotate_on_error_codes: [...m.rotation.rotate_on_error_codes],
        rotate_on_token_quota: m.rotation.rotate_on_token_quota,
        retry_on_error: m.rotation.retry_on_error,
        retry_initial_delay_ms: m.rotation.retry_initial_delay_ms,
        retry_multiplier: m.rotation.retry_multiplier,
        retry_max_delay_ms: m.rotation.retry_max_delay_ms,
        retry_max: m.rotation.retry_max,
        retry_jitter_pct: m.rotation.retry_jitter_pct,
        max_input_tokens_per_hour: m.limits.max_input_tokens_per_hour,
        max_output_tokens_per_hour: m.limits.max_output_tokens_per_hour,
        max_requests_per_hour: m.limits.max_requests_per_hour,
      }
    }
  }
  return memberForms[keyId]
}

function toggleExpand(keyId: string) {
  if (expandedMemberId.value === keyId) {
    expandedMemberId.value = null
  } else {
    expandedMemberId.value = keyId
    ensureForm(keyId)
  }
}

function addErrorCode(keyId: string) {
  const code = parseInt(newErrorCode.value[keyId] ?? '', 10)
  if (isNaN(code) || code < 100 || code > 599) return
  const form = memberForms[keyId]
  if (form && !form.rotate_on_error_codes.includes(code)) {
    form.rotate_on_error_codes.push(code)
  }
  newErrorCode.value[keyId] = ''
}

function removeErrorCode(keyId: string, code: number) {
  const form = memberForms[keyId]
  if (form) {
    form.rotate_on_error_codes = form.rotate_on_error_codes.filter((c) => c !== code)
  }
}

async function saveMemberConfig(keyId: string) {
  const form = memberForms[keyId]
  if (!form) return
  savingMemberId.value = keyId
  const patch: MemberPatch = {
    rotate_on_error_codes: form.rotate_on_error_codes,
    rotate_on_token_quota: form.rotate_on_token_quota,
    retry_on_error: form.retry_on_error,
    retry_initial_delay_ms: form.retry_initial_delay_ms,
    retry_multiplier: form.retry_multiplier,
    retry_max_delay_ms: form.retry_max_delay_ms,
    retry_max: form.retry_max,
    retry_jitter_pct: form.retry_jitter_pct,
    max_input_tokens_per_hour: form.max_input_tokens_per_hour,
    max_output_tokens_per_hour: form.max_output_tokens_per_hour,
    max_requests_per_hour: form.max_requests_per_hour,
  }
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
  const key = carriedKeyFor(keyId)
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
    delete memberForms[keyId]
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
    await keyGroupsApi.remove(groupId.value)
    await router.replace({ name: 'keys.groupList', params: { projectId: projectId.value } })
  } catch {
    toast.error(t('keys.groups.deleteFailed'))
  }
}

function parseNullableNumber(val: string): number | null {
  return val === '' ? null : Number(val)
}

onMounted(async () => {
  await Promise.all([reload(), reloadCarried()])
})
watch([groupId, projectId], async () => {
  await Promise.all([reload(), reloadCarried()])
})
</script>

<template>
  <main class="p-6">
    <SPageHeader :breadcrumbs="breadcrumbs">
      <template #default>
        <!-- Inline rename title -->
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
      <!-- Section header: Members + Add Member -->
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

      <!-- Empty state -->
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

      <!-- Member cards -->
      <div
        role="list"
        class="flex flex-col gap-2"
      >
        <div
          v-for="m in detail.members"
          :key="m.key_id"
          :data-testid="`member-${m.key_id}`"
        >
          <!-- Member card (drop target) -->
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
            <!-- Drag handle -->
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

            <!-- Priority badge -->
            <SBadge variant="info">
              #{{ m.priority }}
            </SBadge>

            <!-- Key info -->
            <CapabilityChip
              v-if="carriedKeyFor(m.key_id)"
              :provider="carriedKeyFor(m.key_id)!.provider"
            />
            <span class="text-sm truncate max-w-[30ch]">
              {{ carriedKeyFor(m.key_id)?.name ?? m.key_id }}
            </span>
            <code class="text-xs font-mono text-[var(--color-muted)]">
              {{ carriedKeyFor(m.key_id)?.masked_preview ?? '' }}
            </code>

            <div class="ml-auto flex items-center gap-1">
              <!-- Expand toggle -->
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

              <!-- Remove -->
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

          <!-- Expanded config panel -->
          <div
            v-if="expandedMemberId === m.key_id && memberForms[m.key_id]"
            class="border border-t-0 border-[var(--color-border)] rounded-b-[var(--radius-md)] bg-[var(--color-surface)] px-6 py-5"
          >
            <!-- Rotation section -->
            <h3 class="text-sm font-semibold mb-4">
              {{ $t('keys.groups.rotation') }}
            </h3>
            <div class="flex flex-col gap-4">
              <!-- Error codes -->
              <SFormField
                :label="$t('keys.groups.errorCodes')"
                name="error-codes"
              >
                <div class="flex flex-wrap items-center gap-1">
                  <SBadge
                    v-for="code in memberForms[m.key_id].rotate_on_error_codes"
                    :key="code"
                    variant="neutral"
                    removable
                    @remove="removeErrorCode(m.key_id, code)"
                  >
                    {{ code }}
                  </SBadge>
                  <div class="flex items-center gap-1">
                    <SInput
                      v-model="newErrorCode[m.key_id]"
                      type="number"
                      size="sm"
                      class="w-20"
                      :placeholder="$t('keys.groups.codeHint')"
                      @keydown.enter="addErrorCode(m.key_id)"
                    />
                    <SButton
                      variant="ghost"
                      icon-only
                      size="sm"
                      @click="addErrorCode(m.key_id)"
                    >
                      <PlusIcon class="w-4 h-4" />
                    </SButton>
                  </div>
                </div>
              </SFormField>

              <!-- Toggles -->
              <SFormField
                :label="$t('keys.groups.rotateOnQuota')"
                name="rotate-quota"
              >
                <SToggle v-model="memberForms[m.key_id].rotate_on_token_quota" />
              </SFormField>

              <SFormField
                :label="$t('keys.groups.retryOnError')"
                name="retry-error"
              >
                <SToggle v-model="memberForms[m.key_id].retry_on_error" />
              </SFormField>

              <!-- Retry settings -->
              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <SFormField
                  :label="$t('keys.groups.initialDelay')"
                  name="initial-delay"
                >
                  <SInput
                    v-model.number="memberForms[m.key_id].retry_initial_delay_ms"
                    type="number"
                    size="sm"
                  />
                </SFormField>
                <SFormField
                  :label="$t('keys.groups.multiplier')"
                  name="multiplier"
                >
                  <SInput
                    v-model.number="memberForms[m.key_id].retry_multiplier"
                    type="number"
                    size="sm"
                  />
                </SFormField>
                <SFormField
                  :label="$t('keys.groups.maxDelay')"
                  name="max-delay"
                >
                  <SInput
                    v-model.number="memberForms[m.key_id].retry_max_delay_ms"
                    type="number"
                    size="sm"
                  />
                </SFormField>
                <SFormField
                  :label="$t('keys.groups.maxRetries')"
                  name="max-retries"
                >
                  <SInput
                    v-model.number="memberForms[m.key_id].retry_max"
                    type="number"
                    size="sm"
                  />
                </SFormField>
                <SFormField
                  :label="$t('keys.groups.jitter')"
                  name="jitter"
                >
                  <SInput
                    v-model.number="memberForms[m.key_id].retry_jitter_pct"
                    type="number"
                    size="sm"
                  />
                </SFormField>
              </div>
            </div>

            <SDivider class="my-5" />

            <!-- Hourly limits section -->
            <h3 class="text-sm font-semibold mb-4">
              {{ $t('keys.groups.hourlyLimits') }}
            </h3>
            <div class="flex flex-col gap-4">
              <SFormField
                :label="$t('keys.groups.maxInputTokens')"
                name="max-input"
                :help="$t('keys.groups.limitHelp')"
              >
                <SInput
                  :model-value="memberForms[m.key_id].max_input_tokens_per_hour ?? ''"
                  type="number"
                  size="sm"
                  @update:model-value="memberForms[m.key_id].max_input_tokens_per_hour = parseNullableNumber(String($event))"
                />
              </SFormField>
              <SFormField
                :label="$t('keys.groups.maxOutputTokens')"
                name="max-output"
                :help="$t('keys.groups.limitHelp')"
              >
                <SInput
                  :model-value="memberForms[m.key_id].max_output_tokens_per_hour ?? ''"
                  type="number"
                  size="sm"
                  @update:model-value="memberForms[m.key_id].max_output_tokens_per_hour = parseNullableNumber(String($event))"
                />
              </SFormField>
              <SFormField
                :label="$t('keys.groups.maxRequests')"
                name="max-requests"
                :help="$t('keys.groups.limitHelp')"
              >
                <SInput
                  :model-value="memberForms[m.key_id].max_requests_per_hour ?? ''"
                  type="number"
                  size="sm"
                  @update:model-value="memberForms[m.key_id].max_requests_per_hour = parseNullableNumber(String($event))"
                />
              </SFormField>
            </div>

            <!-- Save button -->
            <div class="flex justify-end mt-5">
              <SButton
                variant="primary"
                size="sm"
                :loading="savingMemberId === m.key_id"
                @click="saveMemberConfig(m.key_id)"
              >
                {{ $t('app.save') }}
              </SButton>
            </div>
          </div>
        </div>
      </div>
    </section>
  </main>
</template>
