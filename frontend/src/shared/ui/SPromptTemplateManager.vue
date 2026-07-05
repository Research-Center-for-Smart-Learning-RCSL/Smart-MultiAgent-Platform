<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import SButton from './SButton.vue'
import SCharCount from './SCharCount.vue'
import SCodeEditor from './SCodeEditor.vue'
import SEmptyState from './SEmptyState.vue'
import SFormField from './SFormField.vue'
import SInput from './SInput.vue'
import SModal from './SModal.vue'
import STextarea from './STextarea.vue'

export interface TemplateRow {
  id: string
  name: string
  description: string
  body: string
  version: number
}

withDefaults(
  defineProps<{
    templates: TemplateRow[]
    busy?: boolean
  }>(),
  { busy: false },
)

const emit = defineEmits<{
  create: [value: { name: string; description: string; body: string }]
  update: [value: { id: string; version: number; name: string; description: string; body: string }]
  delete: [id: string]
}>()

const { t } = useI18n()

const NAME_MAX = 100
const DESC_MAX = 300
const BODY_MAX = 100_000

const open = ref(false)
const editingId = ref<string | null>(null)
const editingVersion = ref(0)
const name = ref('')
const description = ref('')
const body = ref('')

function openCreate(): void {
  editingId.value = null
  editingVersion.value = 0
  name.value = ''
  description.value = ''
  body.value = ''
  open.value = true
}

function openEdit(tpl: TemplateRow): void {
  editingId.value = tpl.id
  editingVersion.value = tpl.version
  name.value = tpl.name
  description.value = tpl.description
  body.value = tpl.body
  open.value = true
}

function save(): void {
  if (!name.value.trim()) return
  if (editingId.value === null) {
    emit('create', { name: name.value, description: description.value, body: body.value })
  } else {
    emit('update', {
      id: editingId.value,
      version: editingVersion.value,
      name: name.value,
      description: description.value,
      body: body.value,
    })
  }
  open.value = false
}
</script>

<template>
  <div class="space-y-3">
    <div class="flex items-center justify-between">
      <h4 class="text-sm font-semibold">
        {{ t('promptStudio.templates.title') }}
      </h4>
      <SButton
        variant="secondary"
        size="sm"
        type="button"
        @click="openCreate"
      >
        {{ t('promptStudio.templates.new') }}
      </SButton>
    </div>

    <SEmptyState
      v-if="!templates.length"
      :text="t('promptStudio.templates.empty')"
    />
    <ul
      v-else
      class="divide-y divide-[var(--color-border)] rounded border border-[var(--color-border)]"
    >
      <li
        v-for="tpl in templates"
        :key="tpl.id"
        class="flex items-center justify-between gap-3 px-3 py-2"
      >
        <div class="min-w-0">
          <p class="truncate text-sm font-medium">
            {{ tpl.name }}
          </p>
          <p class="truncate text-xs text-[var(--color-muted)]">
            {{ tpl.description }}
          </p>
        </div>
        <div class="flex shrink-0 gap-1">
          <SButton
            variant="ghost"
            size="sm"
            type="button"
            @click="openEdit(tpl)"
          >
            {{ t('promptStudio.actions.edit') }}
          </SButton>
          <SButton
            variant="ghost"
            size="sm"
            type="button"
            @click="emit('delete', tpl.id)"
          >
            {{ t('promptStudio.actions.remove') }}
          </SButton>
        </div>
      </li>
    </ul>

    <SModal
      :open="open"
      :title="editingId ? t('promptStudio.templates.editTitle') : t('promptStudio.templates.newTitle')"
      size="lg"
      @close="open = false"
    >
      <div class="space-y-4">
        <SFormField
          :label="t('promptStudio.templates.nameLabel')"
          name="tpl_name"
          required
        >
          <SInput
            v-model="name"
            :maxlength="NAME_MAX"
          />
        </SFormField>
        <SFormField
          :label="t('promptStudio.templates.descriptionLabel')"
          name="tpl_desc"
        >
          <STextarea
            v-model="description"
            :rows="2"
            :maxlength="DESC_MAX"
          />
        </SFormField>
        <SFormField
          :label="t('promptStudio.templates.bodyLabel')"
          name="tpl_body"
        >
          <SCodeEditor
            v-model="body"
            language="markdown"
            :rows="10"
          />
        </SFormField>
        <SCharCount
          :current="body.length"
          :max="BODY_MAX"
        />
      </div>
      <template #footer>
        <SButton
          variant="ghost"
          type="button"
          @click="open = false"
        >
          {{ t('app.cancel') }}
        </SButton>
        <SButton
          variant="primary"
          :loading="!!busy"
          :disabled="!name.trim()"
          type="button"
          @click="save"
        >
          {{ t('app.save') }}
        </SButton>
      </template>
    </SModal>
  </div>
</template>
