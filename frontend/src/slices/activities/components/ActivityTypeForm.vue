<script setup lang="ts">
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'

import { SModal, SFormField, SInput, SSelect, SButton } from '@shared/ui'
import { useServerErrors, useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'
import { agentsApi, agentKeys } from '@slices/agents'
import type { ActivityTypeIn, ValidatorKind } from '@shared/api-client'

import SchemaBuilder from './SchemaBuilder.vue'
import { registerActivityType } from '../api'
import {
  VALIDATOR_KINDS,
  activityTypeCreateSchema,
  assembleValidatorConfig,
  type ActivityTypeCreateInput,
} from '../types/schemas'

const props = defineProps<{ projectId: string; open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'created'): void }>()

const { t } = useI18n()
const toast = useToast()

const { handleSubmit, errors, defineField, resetForm, setErrors, setFieldError } =
  useForm<ActivityTypeCreateInput>({
    validationSchema: toTypedSchema(activityTypeCreateSchema),
    initialValues: {
      key: '',
      name: '',
      retention_days: null,
      payload_schema: { type: 'object', properties: {} },
      validator_kind: 'webhook',
      webhook_url: '',
      mcp_agent_id: '',
      mcp_binding_id: '',
      mcp_tool_name: '',
    },
  })

const [key] = defineField('key')
const [name] = defineField('name')
const [retentionDays] = defineField('retention_days')
const [payloadSchema] = defineField('payload_schema')
const [validatorKind] = defineField('validator_kind')
const [webhookUrl] = defineField('webhook_url')
const [mcpAgentId] = defineField('mcp_agent_id')
const [mcpBindingId] = defineField('mcp_binding_id')
const [mcpToolName] = defineField('mcp_tool_name')

// SInput's model accepts `string | number` (not null); retention is nullable
// (blank = no cap). Bridge to '' for display; the field keeps storing number | null.
const retentionDisplay = computed<string | number>({
  get: () => retentionDays.value ?? '',
  set: (v) => {
    retentionDays.value = v === '' || v === null ? null : Number(v)
  },
})

const validatorKindOptions = computed(() =>
  VALIDATOR_KINDS.map((k) => ({ value: k, label: t(`activities.typeForm.validatorKind.${k}`) })),
)

// --- MCP validator pickers (agent + hosted_mcp binding from the same project) ---
const agentsQuery = useQuery({
  queryKey: agentKeys.agents(props.projectId),
  queryFn: () => agentsApi.list(props.projectId),
  enabled: computed(() => validatorKind.value === 'mcp'),
})

const agentOptions = computed(() =>
  (agentsQuery.data.value ?? []).map((a) => ({ value: a.id, label: a.name })),
)

const toolsQuery = useQuery({
  queryKey: computed(() => agentKeys.tools(mcpAgentId.value)),
  queryFn: () => agentsApi.listTools(mcpAgentId.value),
  enabled: computed(() => validatorKind.value === 'mcp' && !!mcpAgentId.value),
})

const bindingOptions = computed(() =>
  (toolsQuery.data.value ?? [])
    .filter((tool) => tool.tool_type === 'hosted_mcp')
    .map((tool) => ({ value: tool.id, label: tool.display_name ?? tool.id })),
)

// Switching agents invalidates any binding chosen under the previous agent.
watch(mcpAgentId, () => {
  mcpBindingId.value = ''
})

const { applyServerErrors } = useServerErrors(setErrors)

const createMutation = useMutation({
  mutationFn: (body: ActivityTypeIn) => registerActivityType(props.projectId, body),
  onSuccess: () => {
    resetForm()
    emit('created')
  },
  onError: (err) => {
    if (applyServerErrors(err)) return
    // Domain 409/422 arrive as a plain ApiError (no per-field payload), so map
    // them to the form ourselves rather than let them fall through unhandled.
    if (err instanceof ApiError && err.status === 409) {
      setFieldError('key', t('activities.typeForm.keyConflict'))
      return
    }
    if (err instanceof ApiError && err.status === 422) {
      toast.error(t('activities.typeForm.configRejected'))
      return
    }
    toast.error(t('activities.typeForm.createFailed'))
  },
})

const onSubmit = handleSubmit((formValues) => {
  createMutation.mutate({
    key: formValues.key,
    name: formValues.name,
    payload_schema: formValues.payload_schema,
    validator_kind: formValues.validator_kind as ValidatorKind,
    validator_config: assembleValidatorConfig(formValues),
    retention_days: formValues.retention_days,
  })
})

function onClose(): void {
  resetForm()
  emit('close')
}
</script>

<template>
  <SModal
    :open="props.open"
    :title="t('activities.typeForm.title')"
    size="lg"
    @close="onClose"
  >
    <form
      id="activity-type-form"
      class="flex flex-col gap-4"
      @submit.prevent="onSubmit"
    >
      <SFormField
        :label="t('activities.typeForm.key')"
        name="key"
        :error="errors.key ?? ''"
        required
      >
        <SInput
          v-model="key"
          :placeholder="t('activities.typeForm.keyPlaceholder')"
          :error="!!errors.key"
          data-testid="type-key"
        />
      </SFormField>

      <SFormField
        :label="t('activities.typeForm.name')"
        name="name"
        :error="errors.name ?? ''"
        required
      >
        <SInput
          v-model="name"
          :error="!!errors.name"
          data-testid="type-name"
        />
      </SFormField>

      <SFormField
        :label="t('activities.typeForm.retentionDays')"
        name="retention_days"
        :error="errors.retention_days ?? ''"
        :help="t('activities.typeForm.retentionHelp')"
      >
        <SInput
          v-model="retentionDisplay"
          type="number"
          min="1"
        />
      </SFormField>

      <SFormField
        :label="t('activities.typeForm.payloadSchema')"
        name="payload_schema"
        :error="errors.payload_schema ? t('activities.typeForm.schemaEmpty') : ''"
        required
      >
        <SchemaBuilder @update:model-value="(s) => (payloadSchema = s)" />
      </SFormField>

      <SFormField
        :label="t('activities.typeForm.validator')"
        name="validator_kind"
        :error="errors.validator_kind ?? ''"
        required
      >
        <SSelect
          v-model="validatorKind"
          :options="validatorKindOptions"
          data-testid="type-validator"
        />
      </SFormField>

      <SFormField
        v-if="validatorKind === 'webhook'"
        :label="t('activities.typeForm.webhookUrl')"
        name="webhook_url"
        :error="errors.webhook_url ? t('activities.typeForm.fieldRequired') : ''"
        required
      >
        <SInput
          v-model="webhookUrl"
          :placeholder="t('activities.typeForm.webhookUrlPlaceholder')"
          :error="!!errors.webhook_url"
          data-testid="type-webhook-url"
        />
      </SFormField>

      <template v-if="validatorKind === 'mcp'">
        <SFormField
          :label="t('activities.typeForm.mcpAgent')"
          name="mcp_agent_id"
          :error="errors.mcp_agent_id ? t('activities.typeForm.fieldRequired') : ''"
          required
        >
          <SSelect
            v-model="mcpAgentId"
            :options="agentOptions"
            :placeholder="t('activities.typeForm.mcpAgentPlaceholder')"
            data-testid="type-mcp-agent"
          />
        </SFormField>

        <SFormField
          :label="t('activities.typeForm.mcpBinding')"
          name="mcp_binding_id"
          :error="errors.mcp_binding_id ? t('activities.typeForm.fieldRequired') : ''"
          required
        >
          <SSelect
            v-model="mcpBindingId"
            :options="bindingOptions"
            :placeholder="t('activities.typeForm.mcpBindingPlaceholder')"
          />
        </SFormField>

        <SFormField
          :label="t('activities.typeForm.mcpToolName')"
          name="mcp_tool_name"
          :error="errors.mcp_tool_name ? t('activities.typeForm.fieldRequired') : ''"
          required
        >
          <SInput
            v-model="mcpToolName"
            :placeholder="t('activities.typeForm.mcpToolNamePlaceholder')"
            :error="!!errors.mcp_tool_name"
          />
        </SFormField>
      </template>
    </form>

    <template #footer>
      <div class="flex justify-end gap-3">
        <SButton
          variant="secondary"
          @click="onClose"
        >
          {{ t('app.cancel') }}
        </SButton>
        <SButton
          variant="primary"
          type="submit"
          form="activity-type-form"
          :loading="createMutation.isPending.value"
        >
          {{ t('activities.typeForm.submit') }}
        </SButton>
      </div>
    </template>
  </SModal>
</template>
