<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigModel } from '../../composables/useConfigModel'
import { SFormField, SInput, STextarea } from '@shared/ui'

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
  const branch = branches[index]
  if (!branch) return
  branch[field] = value
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
    <SFormField
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
              class="text-xs text-danger hover:underline"
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
          <STextarea
            :id="`condition-when-${idx}`"
            :model-value="branch.when"
            mono
            :placeholder="t('workflow.config.when')"
            @update:model-value="updateBranchField(idx, 'when', $event)"
          />

          <label
            :for="`condition-port-${idx}`"
            class="block text-xs font-medium"
          >
            {{ t('workflow.config.port') }}
          </label>
          <SInput
            :id="`condition-port-${idx}`"
            :model-value="branch.port"
            type="text"
            :placeholder="t('workflow.config.port')"
            @update:model-value="updateBranchField(idx, 'port', String($event))"
          />
        </div>
      </div>

      <button
        type="button"
        class="mt-2 text-sm text-accent hover:underline"
        @click="addBranch"
      >
        + {{ t('workflow.config.addBranch') }}
      </button>
    </SFormField>

    <!-- Default port -->
    <SFormField
      :label="t('workflow.config.defaultPort')"
      name="condition-default-port"
    >
      <SInput
        id="condition-default-port"
        :model-value="(local.default_port as string) ?? 'default'"
        type="text"
        @update:model-value="update('default_port', $event)"
      />
    </SFormField>
  </div>
</template>
