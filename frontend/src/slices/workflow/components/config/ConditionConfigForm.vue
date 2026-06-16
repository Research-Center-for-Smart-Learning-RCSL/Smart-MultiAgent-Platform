<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
import FormField from '@shared/ui/FormField.vue'

const { t } = useI18n()

interface Branch {
  when: string
  port: string
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

function toBranches(raw: unknown): Branch[] {
  if (Array.isArray(raw) && raw.length > 0) {
    return raw.map((b) => ({
      when: String(b?.when ?? ''),
      port: String(b?.port ?? ''),
    }))
  }
  return [{ when: '', port: '' }]
}

function getBranches(): Branch[] {
  return toBranches(local.branches)
}

function updateBranchField(index: number, field: keyof Branch, value: string) {
  const branches = structuredClone(getBranches())
  branches[index][field] = value
  update('branches', branches)
}

function addBranch() {
  const branches = structuredClone(getBranches())
  branches.push({ when: '', port: '' })
  update('branches', branches)
}

function removeBranch(index: number) {
  const branches = structuredClone(getBranches())
  branches.splice(index, 1)
  if (branches.length === 0) {
    branches.push({ when: '', port: '' })
  }
  update('branches', branches)
}
</script>

<template>
  <div class="space-y-4">
    <!-- Branches -->
    <FormField
      :label="t('workflow.config.branches')"
      name="condition-branches"
    >
      <div class="space-y-3">
        <div
          v-for="(branch, idx) in getBranches()"
          :key="idx"
          class="border rounded p-2 space-y-2 relative"
        >
          <div class="flex items-start justify-between gap-2">
            <span class="text-xs font-medium text-muted">
              #{{ idx + 1 }}
            </span>
            <button
              type="button"
              class="text-xs text-red-600 hover:text-red-800"
              @click="removeBranch(idx)"
            >
              {{ t('workflow.config.removeBranch') }}
            </button>
          </div>

          <label
            :for="`condition-when-${idx}`"
            class="block text-xs font-medium"
          >
            {{ t('workflow.config.when') }}
          </label>
          <textarea
            :id="`condition-when-${idx}`"
            :value="branch.when"
            class="w-full text-sm border rounded px-2 py-1 bg-bg min-h-[60px] font-mono"
            :placeholder="t('workflow.config.when')"
            @input="updateBranchField(idx, 'when', ($event.target as HTMLTextAreaElement).value)"
          />

          <label
            :for="`condition-port-${idx}`"
            class="block text-xs font-medium"
          >
            {{ t('workflow.config.port') }}
          </label>
          <input
            :id="`condition-port-${idx}`"
            :value="branch.port"
            type="text"
            class="w-full text-sm border rounded px-2 py-1 bg-bg"
            :placeholder="t('workflow.config.port')"
            @input="updateBranchField(idx, 'port', ($event.target as HTMLInputElement).value)"
          >
        </div>
      </div>

      <button
        type="button"
        class="mt-2 text-sm text-blue-600 hover:text-blue-800"
        @click="addBranch"
      >
        + {{ t('workflow.config.addBranch') }}
      </button>
    </FormField>

    <!-- Default port -->
    <FormField
      :label="t('workflow.config.defaultPort')"
      name="condition-default-port"
    >
      <input
        id="condition-default-port"
        :value="local.default_port ?? 'default'"
        type="text"
        class="w-full text-sm border rounded px-2 py-1 bg-bg"
        @input="update('default_port', ($event.target as HTMLInputElement).value)"
      >
    </FormField>
  </div>
</template>
