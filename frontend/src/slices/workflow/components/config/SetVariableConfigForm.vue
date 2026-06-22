<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
import { SFormField } from '@shared/ui'

const { t } = useI18n()

interface Assignment {
  variable: string
  expression: string
}

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

function toAssignments(raw: unknown): Assignment[] {
  if (Array.isArray(raw) && raw.length > 0) {
    return raw.map((a) => ({
      variable: String(a?.variable ?? ''),
      expression: String(a?.expression ?? ''),
    }))
  }
  return [{ variable: '', expression: '' }]
}

function getAssignments(): Assignment[] {
  return toAssignments(local.assignments)
}

function updateAssignment(index: number, field: keyof Assignment, value: string) {
  const assignments = structuredClone(getAssignments())
  assignments[index][field] = value
  update('assignments', assignments)
}

function addAssignment() {
  const assignments = structuredClone(getAssignments())
  assignments.push({ variable: '', expression: '' })
  update('assignments', assignments)
}

function removeAssignment(index: number) {
  const assignments = structuredClone(getAssignments())
  assignments.splice(index, 1)
  if (assignments.length === 0) {
    assignments.push({ variable: '', expression: '' })
  }
  update('assignments', assignments)
}
</script>

<template>
  <div class="space-y-4">
    <SFormField
      :label="t('workflow.config.assignments')"
      name="set-var-assignments"
    >
      <div class="space-y-3">
        <div
          v-for="(assignment, idx) in getAssignments()"
          :key="idx"
          class="border rounded p-2 space-y-2 relative"
        >
          <div class="flex items-start justify-between gap-2">
            <span class="text-xs font-medium text-muted">
              #{{ idx + 1 }}
            </span>
            <button
              type="button"
              class="text-xs text-danger hover:underline"
              @click="removeAssignment(idx)"
            >
              {{ t('workflow.config.removeAssignment') }}
            </button>
          </div>

          <label
            :for="`set-var-variable-${idx}`"
            class="block text-xs font-medium"
          >
            {{ t('workflow.config.variable') }}
          </label>
          <input
            :id="`set-var-variable-${idx}`"
            :value="assignment.variable"
            type="text"
            class="wf-input"
            :placeholder="t('workflow.config.variable')"
            @input="updateAssignment(idx, 'variable', ($event.target as HTMLInputElement).value)"
          >

          <label
            :for="`set-var-expression-${idx}`"
            class="block text-xs font-medium"
          >
            {{ t('workflow.config.expression') }}
          </label>
          <textarea
            :id="`set-var-expression-${idx}`"
            :value="assignment.expression"
            class="wf-input-code"
            :placeholder="t('workflow.config.expression')"
            @input="updateAssignment(idx, 'expression', ($event.target as HTMLTextAreaElement).value)"
          />
        </div>
      </div>

      <button
        type="button"
        class="mt-2 text-sm text-accent hover:underline"
        @click="addAssignment"
      >
        + {{ t('workflow.config.addAssignment') }}
      </button>
    </SFormField>
  </div>
</template>
