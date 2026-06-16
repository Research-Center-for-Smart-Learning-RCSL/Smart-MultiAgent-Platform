<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
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

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T
}

const local = reactive<Record<string, unknown>>({ ...props.modelValue })

watch(() => props.modelValue, (v) => {
  Object.assign(local, clone(v))
}, { deep: true })

function update(field: string, value: unknown) {
  local[field] = value
  emit('update:modelValue', { ...local })
}
</script>

<template>
  <div class="space-y-4">
    <FormField :label="t('workflow.config.description')" name="parallel-description">
      <textarea
        id="parallel-description"
        :value="(local.description as string) ?? ''"
        class="w-full text-sm border rounded px-2 py-1 bg-bg min-h-[60px]"
        @input="update('description', ($event.target as HTMLTextAreaElement).value)"
      />
    </FormField>
  </div>
</template>
