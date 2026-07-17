<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useToast } from '@shared/composables'
import { SAlert, SBadge, SButton, SCodeEditor, SFileUpload, SFormField, SInput } from '@shared/ui'

import { isAssetFile, useSkillFiles } from '../composables/useSkillFiles'
import type { SkillFileOut, SkillScopeRef } from '../types'

const props = withDefaults(
  defineProps<{ scope: SkillScopeRef; skillId: string; disabled?: boolean }>(),
  { disabled: false },
)

const { t } = useI18n()
const toast = useToast()

// Q-17 caps a skill file at 32 MB; SFileUpload rejects an oversize file before it is read.
const MAX_FILE_BYTES = 32 * 1024 * 1024

const {
  filesQuery,
  files,
  pendingFiles,
  quarantinedFiles,
  authoredContent,
  busy,
  addAuthored,
  upload,
  replaceContent,
  remove,
} = useSkillFiles(props.scope, () => props.skillId)

const selectedId = ref<string | null>(null)
const selected = computed<SkillFileOut | null>(
  () => files.value.find((f) => f.id === selectedId.value) ?? null,
)
const editContent = ref('')

function select(f: SkillFileOut): void {
  selectedId.value = f.id
  editContent.value = authoredContent.value[f.id] ?? ''
}

const addMode = ref<'author' | 'upload'>('author')
const newPath = ref('')
const newContent = ref('')
const uploadRef = ref<InstanceType<typeof SFileUpload> | null>(null)
const pendingUpload = ref<File | null>(null)

function onUploadFiles(fs: File[]): void {
  pendingUpload.value = fs[0] ?? null
  if (pendingUpload.value && !newPath.value) newPath.value = `references/${pendingUpload.value.name}`
}

function onUploadError(message: string): void {
  toast.error(message)
}

async function submitAuthored(): Promise<void> {
  if (!newPath.value.trim()) return
  const ok = await addAuthored(newPath.value.trim(), newContent.value)
  if (ok) {
    newPath.value = ''
    newContent.value = ''
  }
}

async function submitUpload(): Promise<void> {
  if (!newPath.value.trim() || !pendingUpload.value) return
  const ok = await upload(newPath.value.trim(), pendingUpload.value)
  if (ok) {
    newPath.value = ''
    pendingUpload.value = null
    uploadRef.value?.clear()
  }
}

async function submitReplace(): Promise<void> {
  if (!selected.value) return
  await replaceContent(selected.value, editContent.value)
}

async function onDelete(f: SkillFileOut): Promise<void> {
  const removed = await remove(f)
  if (removed && selectedId.value === f.id) {
    selectedId.value = null
    editContent.value = ''
  }
}

function scanVariant(status: string): 'success' | 'warning' | 'danger' {
  if (status === 'clean') return 'success'
  if (status === 'pending') return 'warning'
  return 'danger'
}

const selectedIsAsset = computed(() => !!selected.value && isAssetFile(selected.value))
</script>

<template>
  <div class="space-y-4">
    <!-- AC-34: a skill with any non-clean file is unreadable by agents; flag it. -->
    <SAlert
      v-if="quarantinedFiles.length"
      variant="danger"
      :title="t('skills.files.unreadableTitle')"
    >
      {{ t('skills.files.quarantinedBody', { count: quarantinedFiles.length }) }}
    </SAlert>
    <SAlert
      v-else-if="pendingFiles.length"
      variant="warning"
      :title="t('skills.files.scanningTitle')"
    >
      {{ t('skills.files.scanningBody', { count: pendingFiles.length }) }}
    </SAlert>

    <div class="grid gap-4 lg:grid-cols-2">
      <!-- File list -->
      <div>
        <h4 class="mb-2 text-sm font-semibold text-[var(--color-fg)]">
          {{ t('skills.files.listHeading') }}
        </h4>
        <p
          v-if="filesQuery.isLoading.value"
          class="text-sm text-[var(--color-muted)]"
        >
          {{ t('skills.files.loading') }}
        </p>
        <SAlert
          v-else-if="filesQuery.isError.value"
          variant="danger"
        >
          {{ t('skills.files.loadFailed') }}
        </SAlert>
        <p
          v-else-if="!files.length"
          class="text-sm text-[var(--color-muted)]"
        >
          {{ t('skills.files.empty') }}
        </p>
        <ul
          v-else
          class="divide-y divide-[var(--color-border)] rounded-md border border-[var(--color-border)]"
        >
          <li
            v-for="f in files"
            :key="f.id"
            class="flex items-center gap-2 px-3 py-2 text-sm"
            :class="{ 'bg-[var(--color-surface)]': f.id === selectedId }"
          >
            <button
              type="button"
              class="flex-1 truncate text-left hover:underline"
              @click="select(f)"
            >
              <span class="font-mono">{{ f.path }}</span>
            </button>
            <SBadge
              variant="neutral"
              size="sm"
            >
              {{ t(`skills.files.kind.${f.kind}`) }}
            </SBadge>
            <SBadge
              :variant="scanVariant(f.scan_status)"
              size="sm"
            >
              {{ t(`skills.files.scan.${f.scan_status}`) }}
            </SBadge>
            <SButton
              variant="ghost"
              size="sm"
              :disabled="disabled || busy"
              @click="onDelete(f)"
            >
              {{ t('skills.actions.remove') }}
            </SButton>
          </li>
        </ul>
      </div>

      <!-- Add / edit -->
      <div class="space-y-4">
        <div v-if="!disabled">
          <h4 class="mb-2 text-sm font-semibold text-[var(--color-fg)]">
            {{ t('skills.files.addHeading') }}
          </h4>
          <div class="mb-3 flex gap-2">
            <SButton
              :variant="addMode === 'author' ? 'primary' : 'secondary'"
              size="sm"
              @click="addMode = 'author'"
            >
              {{ t('skills.files.modeAuthor') }}
            </SButton>
            <SButton
              :variant="addMode === 'upload' ? 'primary' : 'secondary'"
              size="sm"
              @click="addMode = 'upload'"
            >
              {{ t('skills.files.modeUpload') }}
            </SButton>
          </div>

          <SFormField
            :label="t('skills.files.pathLabel')"
            name="skill-file-path"
            :help="t('skills.files.pathHelp')"
          >
            <SInput
              v-model="newPath"
              placeholder="references/example.md"
            />
          </SFormField>

          <template v-if="addMode === 'author'">
            <div class="mt-3">
              <SCodeEditor
                v-model="newContent"
                language="markdown"
                :rows="8"
                :placeholder="t('skills.files.contentPlaceholder')"
              />
            </div>
            <SButton
              class="mt-3"
              variant="primary"
              size="sm"
              :loading="busy"
              :disabled="!newPath.trim()"
              @click="submitAuthored"
            >
              {{ t('skills.files.addButton') }}
            </SButton>
          </template>

          <template v-else>
            <div class="mt-3">
              <SFileUpload
                ref="uploadRef"
                :max-size="MAX_FILE_BYTES"
                @files="onUploadFiles"
                @error="onUploadError"
              />
            </div>
            <SButton
              class="mt-3"
              variant="primary"
              size="sm"
              :loading="busy"
              :disabled="!newPath.trim() || !pendingUpload"
              @click="submitUpload"
            >
              {{ t('skills.files.uploadButton') }}
            </SButton>
          </template>
        </div>

        <!-- Selected-file editor -->
        <div v-if="selected">
          <h4 class="mb-2 text-sm font-semibold text-[var(--color-fg)]">
            {{ t('skills.files.editHeading', { path: selected.path }) }}
          </h4>
          <!-- AC-17: asset binaries are opaque bytes; the editor is gated by kind. -->
          <SAlert
            v-if="selectedIsAsset"
            variant="info"
          >
            {{ t('skills.files.assetNotEditable') }}
          </SAlert>
          <template v-else>
            <p class="mb-2 text-xs text-[var(--color-muted)]">
              {{ t('skills.files.replaceNotice') }}
            </p>
            <SCodeEditor
              v-model="editContent"
              language="markdown"
              :rows="10"
              :readonly="!!disabled"
            />
            <SButton
              class="mt-3"
              variant="primary"
              size="sm"
              :loading="busy"
              :disabled="!!disabled"
              @click="submitReplace"
            >
              {{ t('skills.files.replaceButton') }}
            </SButton>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
