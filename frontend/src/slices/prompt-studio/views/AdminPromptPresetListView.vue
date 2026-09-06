<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { PlusIcon } from '@heroicons/vue/24/outline'

import { useConfirmDialog, useToast } from '@shared/composables'
import { SBadge, SButton, SEmptyState, SPageHeader, SQueryError, STable } from '@shared/ui'
import type { Column } from '@shared/ui/STable.vue'

import { useDeletePresetMutation, usePresetsQuery } from '../queries'
import type { AssistantConfig } from '../types'

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()

const breadcrumbs = computed(() => [
  { label: t('promptStudio.admin.title'), to: { name: 'admin.promptStudio' } },
])

const presetsQuery = usePresetsQuery()
const deleteMutation = useDeletePresetMutation()

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('promptStudio.presets.nameColumn') },
  { key: 'description', label: t('promptStudio.presets.descriptionColumn') },
  { key: 'enabled', label: t('promptStudio.presets.statusColumn'), width: '120px' },
  { key: 'actions', label: '', width: '160px' },
])

type PresetRow = AssistantConfig & Record<string, unknown>
const rows = computed<PresetRow[]>(() => (presetsQuery.data.value ?? []) as unknown as PresetRow[])

async function onDelete(preset: AssistantConfig): Promise<void> {
  const ok = await confirm({
    title: t('promptStudio.presets.deleteTitle'),
    message: t('promptStudio.presets.deleteBody', { name: preset.name }),
    variant: 'error',
  })
  if (!ok) return
  try {
    await deleteMutation.mutateAsync(preset.id)
    toast.success(t('promptStudio.presets.deleted'))
  } catch {
    toast.error(t('promptStudio.presets.deleteFailed'))
  }
}
</script>

<template>
  <div class="mx-auto max-w-4xl">
    <SPageHeader
      :title="t('promptStudio.presets.title')"
      :subtitle="t('promptStudio.presets.subtitle')"
      :breadcrumbs="breadcrumbs"
    >
      <template #actions>
        <SButton
          variant="primary"
          as="router-link"
          :to="{ name: 'admin.promptPresetNew' }"
        >
          <template #icon-left>
            <PlusIcon class="h-4 w-4" />
          </template>
          {{ t('promptStudio.presets.newPreset') }}
        </SButton>
      </template>
    </SPageHeader>

    <SQueryError
      v-if="presetsQuery.isError.value"
      class="mt-4"
      :message="t('promptStudio.config.loadFailed')"
      :retry-label="t('admin.common.retry')"
      @retry="presetsQuery.refetch()"
    />

    <STable
      v-else
      class="mt-4"
      :columns="columns"
      :data="rows"
      :loading="presetsQuery.isPending.value"
      row-key="id"
    >
      <template #cell-enabled="{ row }">
        <SBadge
          size="sm"
          :variant="row.enabled ? 'success' : 'neutral'"
        >
          {{ row.enabled ? t('promptStudio.presets.active') : t('promptStudio.presets.inactive') }}
        </SBadge>
      </template>

      <template #cell-actions="{ row }">
        <div class="flex items-center gap-3">
          <router-link
            class="text-[var(--color-accent)] underline"
            :to="{ name: 'admin.promptPresetEdit', params: { presetId: row.id } }"
          >
            {{ t('promptStudio.actions.edit') }}
          </router-link>
          <button
            type="button"
            class="text-[var(--color-danger)] underline"
            @click="onDelete(row)"
          >
            {{ t('promptStudio.actions.remove') }}
          </button>
        </div>
      </template>

      <template #empty>
        <SEmptyState
          :title="t('promptStudio.presets.emptyTitle')"
          :text="t('promptStudio.presets.emptyText')"
        />
      </template>
    </STable>
  </div>
</template>
