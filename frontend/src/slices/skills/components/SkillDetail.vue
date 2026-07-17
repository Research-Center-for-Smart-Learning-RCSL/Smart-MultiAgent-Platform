<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useToast } from '@shared/composables'
import { SAlert, SBadge, SButton, SCodeEditor, SFormField, SInput, STextarea } from '@shared/ui'

import { useBundleTransport } from '../composables/useBundleTransport'
import { useSkillEditor } from '../composables/useSkillEditor'
import { useCopySkillMutation } from '../queries'
import type { SkillScopeRef } from '../types'
import SkillFiles from './SkillFiles.vue'

const props = defineProps<{ scope: SkillScopeRef; skillId: string }>()
const emit = defineEmits<{ deleted: [] }>()

const { t } = useI18n()
const toast = useToast()

const { skillQuery, form, dirty, saving, deleted, save, remove, restore } = useSkillEditor(
  props.scope,
  () => props.skillId,
)

const skill = computed(() => skillQuery.data.value ?? null)

const { exportSkill, exporting } = useBundleTransport(props.scope)
const copyMutation = useCopySkillMutation(props.scope)

const copyName = ref('')
const showCopy = ref(false)

async function onDelete(): Promise<void> {
  if (await remove()) emit('deleted')
}

// Duplicate the skill within its own scope under a new name. Promoting across scopes
// (the more common motivation, since scope is immutable) needs a target-owner picker and
// is deferred (FU).
async function onDuplicate(): Promise<void> {
  const name = copyName.value.trim()
  const s = skill.value
  if (!name || !s) return
  try {
    await copyMutation.mutateAsync({
      skillId: props.skillId,
      body: { target_scope: s.scope, target_owner_id: s.owner_id, name },
    })
    toast.success(t('skills.editor.duplicated'))
    copyName.value = ''
    showCopy.value = false
  } catch {
    toast.error(t('skills.editor.duplicateFailed'))
  }
}
</script>

<template>
  <div>
    <div
      v-if="skillQuery.isLoading.value"
      class="p-6 text-sm text-[var(--color-muted)]"
    >
      {{ t('skills.editor.loading') }}
    </div>
    <SAlert
      v-else-if="skillQuery.isError.value"
      variant="danger"
    >
      {{ t('skills.editor.loadFailed') }}
    </SAlert>

    <div
      v-else-if="skill"
      class="space-y-5"
    >
      <!-- Header: immutable name + scope/source/state badges -->
      <div class="flex flex-wrap items-center gap-2">
        <h3 class="font-mono text-lg font-semibold text-[var(--color-fg)]">
          {{ skill.name }}
        </h3>
        <SBadge variant="info">
          {{ t(`skills.scope.${skill.scope}`) }}
        </SBadge>
        <SBadge variant="neutral">
          {{ skill.source }}
        </SBadge>
        <SBadge
          v-if="skill.diverged"
          variant="warning"
        >
          {{ t('skills.editor.diverged') }}
        </SBadge>
        <SBadge
          v-if="deleted"
          variant="danger"
        >
          {{ t('skills.editor.deletedBadge') }}
        </SBadge>
      </div>

      <SAlert
        v-if="deleted"
        variant="warning"
      >
        {{ t('skills.editor.deletedNotice') }}
      </SAlert>

      <SFormField
        :label="t('skills.editor.description')"
        name="skill-description"
        :help="t('skills.editor.descriptionHelp')"
      >
        <STextarea
          v-model="form.description"
          :rows="2"
          :maxlength="1024"
          :disabled="deleted"
        />
      </SFormField>

      <SFormField
        :label="t('skills.editor.body')"
        name="skill-body"
      >
        <SCodeEditor
          v-model="form.body"
          language="markdown"
          :rows="16"
          :readonly="deleted"
        />
      </SFormField>

      <div class="grid gap-4 md:grid-cols-2">
        <SFormField
          :label="t('skills.editor.requires')"
          name="skill-requires"
          :help="t('skills.editor.requiresHelp')"
        >
          <STextarea
            v-model="form.requiresText"
            :rows="3"
            :disabled="deleted"
            placeholder="code_exec"
          />
        </SFormField>

        <SFormField
          :label="t('skills.editor.allowedTools')"
          name="skill-allowed-tools"
        >
          <STextarea
            v-model="form.allowedToolsText"
            :rows="3"
            :disabled="deleted"
          />
        </SFormField>
      </div>
      <p class="-mt-2 text-xs text-[var(--color-muted)]">
        {{ t('skills.editor.allowedToolsNotEnforced') }}
      </p>

      <!-- Actions -->
      <div class="flex flex-wrap gap-2 border-t border-[var(--color-border)] pt-4">
        <SButton
          v-if="!deleted"
          variant="primary"
          :loading="saving"
          :disabled="!dirty"
          @click="save"
        >
          {{ t('skills.actions.save') }}
        </SButton>
        <SButton
          v-if="deleted"
          variant="primary"
          @click="restore"
        >
          {{ t('skills.actions.restore') }}
        </SButton>
        <SButton
          variant="secondary"
          :loading="exporting"
          @click="exportSkill(props.skillId)"
        >
          {{ t('skills.actions.export') }}
        </SButton>
        <SButton
          v-if="!deleted"
          variant="secondary"
          @click="showCopy = !showCopy"
        >
          {{ t('skills.actions.duplicate') }}
        </SButton>
        <SButton
          v-if="!deleted"
          variant="danger"
          @click="onDelete"
        >
          {{ t('skills.actions.delete') }}
        </SButton>
      </div>

      <div
        v-if="showCopy"
        class="flex items-end gap-2 rounded-md border border-[var(--color-border)] p-3"
      >
        <SFormField
          :label="t('skills.editor.duplicateName')"
          name="skill-copy-name"
          class="flex-1"
        >
          <SInput
            v-model="copyName"
            placeholder="my-skill-copy"
          />
        </SFormField>
        <SButton
          variant="primary"
          size="sm"
          :loading="copyMutation.isPending.value"
          :disabled="!copyName.trim()"
          @click="onDuplicate"
        >
          {{ t('skills.actions.duplicate') }}
        </SButton>
      </div>

      <!-- Bundled files -->
      <div class="border-t border-[var(--color-border)] pt-4">
        <h4 class="mb-3 text-sm font-semibold text-[var(--color-fg)]">
          {{ t('skills.files.sectionHeading') }}
        </h4>
        <SkillFiles
          :scope="scope"
          :skill-id="props.skillId"
          :disabled="deleted"
        />
      </div>
    </div>
  </div>
</template>
