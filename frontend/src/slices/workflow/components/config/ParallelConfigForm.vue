<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
import { SFormField, STextarea } from '@shared/ui'

const { t } = useI18n()

const props = defineProps<{
  modelValue: Record<string, unknown>
  agents: Array<{ id: string; name: string }>
  chatrooms: Array<{ id: string; name: string }>
  allNodeIds: string[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
}>()

const { local, update } = useConfigModel(props, emit)
</script>

<template>
  <div class="space-y-4">
    <SFormField
      :label="t('workflow.config.description')"
      name="parallel-description"
    >
      <STextarea
        id="parallel-description"
        :model-value="(local.description as string) ?? ''"
        class="min-h-[60px]"
        @update:model-value="update('description', $event)"
      />
    </SFormField>
  </div>
</template>
