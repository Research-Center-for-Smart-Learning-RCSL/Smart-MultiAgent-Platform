<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
import FormField from '@shared/ui/FormField.vue'

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
    <FormField
      :label="t('workflow.config.description')"
      name="parallel-description"
    >
      <textarea
        id="parallel-description"
        :value="(local.description as string) ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg min-h-[60px]"
        @input="update('description', ($event.target as HTMLTextAreaElement).value)"
      />
    </FormField>
  </div>
</template>
