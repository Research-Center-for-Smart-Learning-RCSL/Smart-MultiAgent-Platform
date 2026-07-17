<script setup lang="ts">
import { computed, reactive } from 'vue'
import { useI18n } from 'vue-i18n'

import { useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import { SButton, SCodeEditor, SFormField, SInput, STextarea } from '@shared/ui'

import { parseList } from '../lib/parseList'
import { useCreateSkillMutation } from '../queries'
import type { SkillOut, SkillScopeRef } from '../types'

const props = defineProps<{ scope: SkillScopeRef }>()
const emit = defineEmits<{ created: [skill: SkillOut]; cancel: [] }>()

const { t } = useI18n()
const toast = useToast()
const createMutation = useCreateSkillMutation(props.scope)

// R31.01: lowercase alphanumeric with hyphens, 1-64 chars — also the directory name under
// /workspace/skills/, so it is immutable after create. Checked client-side to disable
// submit; the backend is authoritative (422).
const NAME_RE = /^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$/

const form = reactive({ name: '', description: '', body: '', requiresText: '', allowedToolsText: '' })

const nameValid = computed(() => NAME_RE.test(form.name))
const nameError = computed(() => (form.name && !nameValid.value ? t('skills.create.nameInvalid') : ''))
const canSubmit = computed(() => nameValid.value && form.description.trim().length > 0)

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  try {
    const skill = await createMutation.mutateAsync({
      name: form.name,
      description: form.description,
      body: form.body,
      requires: parseList(form.requiresText),
      allowed_tools: parseList(form.allowedToolsText),
    })
    toast.success(t('skills.create.created'))
    emit('created', skill)
  } catch (err) {
    if (isProblemWithType(err, 'skills/name-taken')) {
      toast.error(t('skills.create.nameTaken'))
    } else {
      toast.error(t('skills.create.failed'))
    }
  }
}
</script>

<template>
  <div class="space-y-4">
    <h3 class="text-lg font-semibold text-[var(--color-fg)]">
      {{ t('skills.create.heading') }}
    </h3>

    <SFormField
      :label="t('skills.create.name')"
      name="new-skill-name"
      :help="t('skills.create.nameHelp')"
      :error="nameError"
      required
    >
      <SInput
        v-model="form.name"
        placeholder="pdf-fill"
        :error="!!form.name && !nameValid"
      />
    </SFormField>

    <SFormField
      :label="t('skills.editor.description')"
      name="new-skill-description"
      :help="t('skills.editor.descriptionHelp')"
      required
    >
      <STextarea
        v-model="form.description"
        :rows="2"
        :maxlength="1024"
      />
    </SFormField>

    <SFormField
      :label="t('skills.editor.body')"
      name="new-skill-body"
    >
      <SCodeEditor
        v-model="form.body"
        language="markdown"
        :rows="10"
        :placeholder="t('skills.create.bodyPlaceholder')"
      />
    </SFormField>

    <div class="grid gap-4 md:grid-cols-2">
      <SFormField
        :label="t('skills.editor.requires')"
        name="new-skill-requires"
        :help="t('skills.editor.requiresHelp')"
      >
        <STextarea
          v-model="form.requiresText"
          :rows="3"
          placeholder="code_exec"
        />
      </SFormField>
      <SFormField
        :label="t('skills.editor.allowedTools')"
        name="new-skill-allowed-tools"
      >
        <STextarea
          v-model="form.allowedToolsText"
          :rows="3"
        />
      </SFormField>
    </div>
    <p class="-mt-2 text-xs text-[var(--color-muted)]">
      {{ t('skills.editor.allowedToolsNotEnforced') }}
    </p>

    <div class="flex gap-2">
      <SButton
        variant="primary"
        :loading="createMutation.isPending.value"
        :disabled="!canSubmit"
        @click="submit"
      >
        {{ t('skills.create.submit') }}
      </SButton>
      <SButton
        variant="ghost"
        @click="emit('cancel')"
      >
        {{ t('skills.actions.cancel') }}
      </SButton>
    </div>
  </div>
</template>
