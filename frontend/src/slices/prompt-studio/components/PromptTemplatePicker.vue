<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { SButton, SEmptyState, SLoadingSpinner, SModal } from '@shared/ui'

import { useProjectTemplatesQuery } from '../queries'
import type { PromptScope, PromptTemplate } from '../types'

const props = defineProps<{
  projectId: string
}>()

const emit = defineEmits<{
  insert: [body: string]
}>()

const { t } = useI18n()

const open = ref(false)
const query = useProjectTemplatesQuery(() => props.projectId)

const SCOPES: PromptScope[] = ['platform', 'org', 'user']

const grouped = computed<{ scope: PromptScope; templates: PromptTemplate[] }[]>(() => {
  const all = query.data.value ?? []
  return SCOPES.map((scope) => ({ scope, templates: all.filter((tpl) => tpl.scope === scope) })).filter(
    (g) => g.templates.length > 0,
  )
})

function insert(tpl: PromptTemplate): void {
  emit('insert', tpl.body)
  open.value = false
}
</script>

<template>
  <div>
    <SButton
      variant="secondary"
      size="sm"
      type="button"
      @click="open = true"
    >
      {{ t('promptStudio.picker.button') }}
    </SButton>

    <SModal
      :open="open"
      :title="t('promptStudio.picker.title')"
      size="lg"
      @close="open = false"
    >
      <div
        v-if="query.isLoading.value"
        class="flex justify-center p-6"
      >
        <SLoadingSpinner size="sm" />
      </div>
      <SEmptyState
        v-else-if="!grouped.length"
        :text="t('promptStudio.picker.empty')"
      />
      <div
        v-else
        class="space-y-5"
      >
        <section
          v-for="group in grouped"
          :key="group.scope"
        >
          <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
            {{ t(`promptStudio.scope.${group.scope}`) }}
          </h4>
          <ul class="divide-y divide-[var(--color-border)] rounded border border-[var(--color-border)]">
            <li
              v-for="tpl in group.templates"
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
              <SButton
                variant="ghost"
                size="sm"
                type="button"
                @click="insert(tpl)"
              >
                {{ t('promptStudio.picker.insert') }}
              </SButton>
            </li>
          </ul>
        </section>
      </div>
    </SModal>
  </div>
</template>
