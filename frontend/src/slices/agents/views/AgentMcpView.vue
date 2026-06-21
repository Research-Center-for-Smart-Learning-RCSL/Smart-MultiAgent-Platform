<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { SFormField } from '@shared/ui'
import { useConfirmDialog, useServerErrors, useToast } from '@shared/composables'
import { agentsApi, type McpBinding, type McpTestResult } from '../api'
import { agentKeys } from '../queries'
import { mcpBindingCreateSchema, type McpBindingCreateInput } from '../types/schemas'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const toast = useToast()
const { confirm } = useConfirmDialog()
const agentId = route.params.agentId as string

// The three built-in tool names a `builtin`-source binding can reference.
const BUILTIN_TOOLS = ['file', 'web_search', 'code_exec']

const showForm = ref(false)

const agentQuery = useQuery({
  queryKey: agentKeys.agent(agentId),
  queryFn: async () => (await agentsApi.get(agentId)).data,
})
const projectId = computed(() => agentQuery.data.value?.project_id ?? '')

const bindingsQuery = useQuery({
  queryKey: agentKeys.mcpBindings(agentId),
  queryFn: async () => (await agentsApi.listMcpBindings(agentId)).data,
})

const schema = toTypedSchema(mcpBindingCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors } =
  useForm<McpBindingCreateInput>({
    validationSchema: schema,
    initialValues: { source: 'url', reference: '', allowed_tools: [], config: {} },
  })

const [source] = defineField('source')
const [reference] = defineField('reference')

// The valid `reference` space differs per source (built-in tool name vs URL vs
// package spec), so clear it on source change — otherwise a URL typed under
// 'url' could be submitted as a 'builtin' reference.
watch(source, () => {
  reference.value = ''
})

// allowed_tools is a list; collect it as a comma/space-separated string and
// parse at submit (kept outside vee-validate, so reset by hand).
const allowedToolsRaw = ref('')
function parseTools(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

const { applyServerErrors } = useServerErrors(setErrors)

function resetCreateForm(): void {
  resetForm()
  allowedToolsRaw.value = ''
}

const createMutation = useMutation({
  mutationFn: async (payload: McpBindingCreateInput) =>
    (await agentsApi.addMcpBinding(agentId, payload)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.mcpBindings(agentId) })
    resetCreateForm()
    showForm.value = false
    toast.success(t('agents.mcp.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.mcp.createFailed'))
  },
})

const onSubmit = handleSubmit((values) =>
  createMutation.mutate({ ...values, allowed_tools: parseTools(allowedToolsRaw.value) }),
)

// Test results + in-flight state are keyed by binding id so a slow/hung probe
// on one binding only disables that row's Test button, not every row's.
const testResults = ref<Record<string, McpTestResult>>({})
const testingIds = ref<Set<string>>(new Set())
const isTesting = (bindingId: string): boolean => testingIds.value.has(bindingId)

const testMutation = useMutation({
  mutationFn: (bindingId: string) => agentsApi.testMcpBinding(agentId, bindingId),
  onSuccess: (res, bindingId) => {
    testResults.value = { ...testResults.value, [bindingId]: res.data }
  },
  onError: () => toast.error(t('agents.mcp.testFailed')),
  onSettled: (_res, _err, bindingId) => {
    const next = new Set(testingIds.value)
    next.delete(bindingId)
    testingIds.value = next
  },
})

function runTest(bindingId: string): void {
  testingIds.value = new Set(testingIds.value).add(bindingId)
  testMutation.mutate(bindingId)
}

const deleteMutation = useMutation({
  mutationFn: (bindingId: string) => agentsApi.deleteMcpBinding(agentId, bindingId),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.mcpBindings(agentId) })
    toast.success(t('agents.mcp.deleted'))
  },
  onError: () => toast.error(t('agents.mcp.deleteFailed')),
})

async function confirmDelete(b: McpBinding): Promise<void> {
  const ok = await confirm({ title: t('agents.mcp.deleteTitle'), message: t('agents.mcp.deleteConfirm', { ref: b.reference }), variant: 'warning' })
  if (!ok) return
  deleteMutation.mutate(b.id)
}
</script>

<template>
  <section class="agent-mcp p-6">
    <div class="agent-mcp__header">
      <h1 class="text-xl font-semibold mb-1">
        {{ t('agents.mcp.title') }}
      </h1>
      <button
        class="btn btn-primary"
        @click="showForm = !showForm"
      >
        {{ showForm ? t('agents.mcp.cancel') : t('agents.mcp.add') }}
      </button>
    </div>
    <p class="agent-mcp__subtitle mb-4">
      {{ t('agents.mcp.subtitle', { name: agentQuery.data.value?.name ?? '' }) }}
      <RouterLink
        v-if="projectId"
        :to="{ name: 'agents.egressAllowlist', params: { projectId } }"
      >
        {{ t('agents.mcp.manageEgress') }}
      </RouterLink>
    </p>

    <form
      v-if="showForm"
      class="agent-mcp__form"
      @submit.prevent="onSubmit"
    >
      <SFormField
        :label="t('agents.mcp.source')"
        name="source"
        :error="errors.source"
        required
      >
        <select
          id="source"
          v-model="source"
        >
          <option value="url">
            {{ t('agents.mcp.sourceUrl') }}
          </option>
          <option value="package">
            {{ t('agents.mcp.sourcePackage') }}
          </option>
          <option value="builtin">
            {{ t('agents.mcp.sourceBuiltin') }}
          </option>
        </select>
      </SFormField>

      <SFormField
        :label="t('agents.mcp.reference')"
        name="reference"
        :error="errors.reference"
        required
      >
        <select
          v-if="source === 'builtin'"
          id="reference"
          v-model="reference"
        >
          <option
            value=""
            disabled
          >
            {{ t('agents.mcp.referencePlaceholderBuiltin') }}
          </option>
          <option
            v-for="tool in BUILTIN_TOOLS"
            :key="tool"
            :value="tool"
          >
            {{ tool }}
          </option>
        </select>
        <input
          v-else
          id="reference"
          v-model="reference"
          :placeholder="source === 'url'
            ? t('agents.mcp.referencePlaceholderUrl')
            : t('agents.mcp.referencePlaceholderPackage')"
          :aria-invalid="!!errors.reference"
        >
      </SFormField>

      <SFormField
        :label="t('agents.mcp.allowedTools')"
        name="allowed_tools"
      >
        <input
          id="allowed_tools"
          v-model="allowedToolsRaw"
          :placeholder="t('agents.mcp.allowedToolsPlaceholder')"
        >
        <span class="agent-mcp__hint">{{ t('agents.mcp.allowedToolsHint') }}</span>
      </SFormField>

      <button
        type="submit"
        class="btn btn-primary"
        :disabled="createMutation.isPending.value"
      >
        {{ t('agents.mcp.submit') }}
      </button>
    </form>

    <p v-if="bindingsQuery.isLoading.value">
      {{ t('agents.mcp.loading') }}
    </p>
    <table
      v-else
      class="agent-mcp__table"
    >
      <thead>
        <tr>
          <th>{{ t('agents.mcp.colSource') }}</th>
          <th>{{ t('agents.mcp.colReference') }}</th>
          <th>{{ t('agents.mcp.colTools') }}</th>
          <th>{{ t('agents.mcp.colActions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="b in bindingsQuery.data.value ?? []"
          :key="b.id"
        >
          <td>{{ b.source }}</td>
          <td class="agent-mcp__ref">
            {{ b.reference }}
          </td>
          <td>
            {{ b.allowed_tools.length ? b.allowed_tools.join(', ') : '—' }}
            <span
              v-if="testResults[b.id]"
              :class="testResults[b.id]!.ok ? 'agent-mcp__ok' : 'agent-mcp__error'"
              :title="testResults[b.id]!.error ?? testResults[b.id]!.tool_names.join(', ')"
            >
              {{ testResults[b.id]!.ok
                ? t('agents.mcp.testOk', { count: testResults[b.id]!.tool_names.length, ms: testResults[b.id]!.duration_ms })
                : t('agents.mcp.testBad') }}
            </span>
          </td>
          <td>
            <button
              class="btn"
              type="button"
              :disabled="isTesting(b.id)"
              @click="runTest(b.id)"
            >
              {{ t('agents.mcp.test') }}
            </button>
            <button
              class="btn btn-danger"
              type="button"
              @click="confirmDelete(b)"
            >
              {{ t('agents.mcp.delete') }}
            </button>
          </td>
        </tr>
        <tr v-if="(bindingsQuery.data.value ?? []).length === 0">
          <td colspan="4">
            {{ t('agents.mcp.empty') }}
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.agent-mcp__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.agent-mcp__subtitle {
  color: var(--color-muted);
}
.agent-mcp__form {
  max-width: 480px;
  margin-bottom: var(--space-6);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.agent-mcp__hint {
  display: block;
  color: var(--color-muted);
  font-size: 0.875rem;
  margin-top: var(--space-1);
}
.agent-mcp__table {
  width: 100%;
  border-collapse: collapse;
}
.agent-mcp__table th,
.agent-mcp__table td {
  text-align: left;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.agent-mcp__ref {
  font-family: var(--font-mono, monospace);
  word-break: break-all;
}
.agent-mcp__ok {
  color: var(--color-success, #15803d);
  margin-left: var(--space-2);
}
.agent-mcp__error {
  color: var(--color-danger, #b91c1c);
  margin-left: var(--space-2);
  cursor: help;
}
</style>
