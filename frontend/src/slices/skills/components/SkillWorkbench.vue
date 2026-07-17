<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useToast } from '@shared/composables'
import { SAlert, SBadge, SButton, SEmptyState, SFileUpload, SLoadingSpinner, SToggle } from '@shared/ui'

import { useBundleTransport } from '../composables/useBundleTransport'
import { useSkillsQuery } from '../queries'
import type { SkillOut, SkillScopeRef, SkillSummaryOut } from '../types'
import SkillCreateForm from './SkillCreateForm.vue'
import SkillDetail from './SkillDetail.vue'

const props = defineProps<{ scope: SkillScopeRef }>()

const { t } = useI18n()
const toast = useToast()

const includeDeleted = ref(false)
const skillsQuery = useSkillsQuery(props.scope, includeDeleted)
const skills = computed<SkillSummaryOut[]>(() => skillsQuery.data.value?.items ?? [])

const selectedId = ref<string | null>(null)
const creating = ref(false)
const showImport = ref(false)

const { importFile, importing } = useBundleTransport(props.scope)

function onImportFiles(fs: File[]): void {
  const file = fs[0]
  if (file) {
    importFile(file)
    showImport.value = false
  }
}

function onImportError(message: string): void {
  toast.error(message)
}

function selectSkill(id: string): void {
  selectedId.value = id
  creating.value = false
}

function startCreate(): void {
  creating.value = true
  selectedId.value = null
}

function onCreated(skill: SkillOut): void {
  creating.value = false
  selectedId.value = skill.id
}

function onDeleted(): void {
  selectedId.value = null
}
</script>

<template>
  <div class="space-y-4">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-3">
      <SButton
        variant="primary"
        size="sm"
        @click="startCreate"
      >
        {{ t('skills.workbench.new') }}
      </SButton>
      <SButton
        variant="secondary"
        size="sm"
        :loading="importing"
        @click="showImport = !showImport"
      >
        {{ t('skills.workbench.import') }}
      </SButton>
      <label
        for="skills-show-deleted"
        class="ml-auto flex items-center gap-2 text-sm text-[var(--color-muted)]"
      >
        <SToggle
          id="skills-show-deleted"
          v-model="includeDeleted"
          size="sm"
        />
        {{ t('skills.workbench.showDeleted') }}
      </label>
    </div>

    <div v-if="showImport">
      <SFileUpload
        accept=".zip,application/zip"
        @files="onImportFiles"
        @error="onImportError"
      />
      <p class="mt-1 text-xs text-[var(--color-muted)]">
        {{ t('skills.workbench.importHelp') }}
      </p>
    </div>

    <div class="grid gap-4 lg:grid-cols-[18rem_1fr]">
      <!-- Skill list -->
      <div class="rounded-md border border-[var(--color-border)]">
        <div
          v-if="skillsQuery.isLoading.value"
          class="flex justify-center p-6"
        >
          <SLoadingSpinner size="sm" />
        </div>
        <SAlert
          v-else-if="skillsQuery.isError.value"
          variant="danger"
        >
          {{ t('skills.workbench.loadFailed') }}
        </SAlert>
        <p
          v-else-if="!skills.length"
          class="p-4 text-sm text-[var(--color-muted)]"
        >
          {{ t('skills.workbench.empty') }}
        </p>
        <ul
          v-else
          class="divide-y divide-[var(--color-border)]"
        >
          <li
            v-for="s in skills"
            :key="s.id"
          >
            <button
              type="button"
              class="flex w-full flex-col gap-1 px-3 py-2 text-left hover:bg-[var(--color-surface)]"
              :class="{ 'bg-[var(--color-surface)]': s.id === selectedId }"
              @click="selectSkill(s.id)"
            >
              <span class="flex items-center gap-2">
                <span class="truncate font-mono text-sm font-medium text-[var(--color-fg)]">{{ s.name }}</span>
                <SBadge
                  v-if="s.deleted_at"
                  variant="danger"
                  size="sm"
                >
                  {{ t('skills.editor.deletedBadge') }}
                </SBadge>
              </span>
              <span class="truncate text-xs text-[var(--color-muted)]">{{ s.description }}</span>
            </button>
          </li>
        </ul>
      </div>

      <!-- Detail / create -->
      <div class="rounded-md border border-[var(--color-border)] p-4">
        <SkillCreateForm
          v-if="creating"
          :scope="scope"
          @created="onCreated"
          @cancel="creating = false"
        />
        <SkillDetail
          v-else-if="selectedId"
          :key="selectedId"
          :scope="scope"
          :skill-id="selectedId"
          @deleted="onDeleted"
        />
        <SEmptyState
          v-else
          :title="t('skills.workbench.pickTitle')"
          :description="t('skills.workbench.pickBody')"
        />
      </div>
    </div>
  </div>
</template>
