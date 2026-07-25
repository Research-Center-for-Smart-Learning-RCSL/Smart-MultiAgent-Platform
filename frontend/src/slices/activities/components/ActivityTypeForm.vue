<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'

import { SModal, SFormField, SInput, SSelect, SCheckbox, SButton } from '@shared/ui'
import { useServerErrors, useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'
import { agentsApi, agentKeys } from '@slices/agents'
import type {
  ActivityTypeIn,
  ActivityTypeOut,
  ActivityTypeUpdateIn,
  ValidatorKind,
} from '@shared/api-client'

import PayloadSchemaField from './PayloadSchemaField.vue'
import { listActivityValidators, registerActivityType, updateActivityType } from '../api'
import { activityKeys } from '../queries'
import {
  EXACT_MATCH_VALIDATOR_ID,
  VALIDATOR_KINDS,
  activityTypeCreateSchema,
  assembleValidatorConfig,
  type ActivityTypeCreateInput,
  type ValidatorKindOption,
} from '../types/schemas'

const props = defineProps<{ projectId: string; open: boolean; editType?: ActivityTypeOut | null }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'created'): void; (e: 'updated'): void }>()

const { t } = useI18n()
const toast = useToast()

const isEdit = computed(() => !!props.editType)

// Rebuild the flat form fields from a stored type (edit pre-fill); the validator
// sub-fields are disassembled back out of validator_config.
function toFormValues(row: ActivityTypeOut | null): Partial<ActivityTypeCreateInput> {
  if (!row) {
    return {
      key: '',
      name: '',
      retention_days: null,
      expose_payload_to_agent: true,
      echo_includes_content: false,
      payload_schema: { type: 'object', properties: {} },
      validator_kind: 'webhook',
      webhook_url: '',
      mcp_agent_id: '',
      mcp_binding_id: '',
      mcp_tool_name: '',
      in_process_validator_id: '',
      exact_match_field: '',
      exact_match_expected: '',
      exact_match_case_sensitive: false,
    }
  }
  const cfg = (row.validator_config ?? {}) as Record<string, unknown>
  return {
    key: row.key,
    name: row.name,
    retention_days: row.retention_days,
    expose_payload_to_agent: row.expose_payload_to_agent,
    echo_includes_content: row.echo_includes_content,
    payload_schema: row.payload_schema,
    validator_kind: row.validator_kind as ValidatorKindOption,
    webhook_url: typeof cfg.url === 'string' ? cfg.url : '',
    mcp_agent_id: typeof cfg.agent_id === 'string' ? cfg.agent_id : '',
    mcp_binding_id: typeof cfg.binding_id === 'string' ? cfg.binding_id : '',
    mcp_tool_name: typeof cfg.tool_name === 'string' ? cfg.tool_name : '',
    in_process_validator_id: typeof cfg.validator_id === 'string' ? cfg.validator_id : '',
    exact_match_field: typeof cfg.field === 'string' ? cfg.field : '',
    exact_match_expected: cfg.expected === undefined || cfg.expected === null ? '' : String(cfg.expected),
    exact_match_case_sensitive: cfg.case_sensitive === true,
  }
}

const { handleSubmit, errors, defineField, resetForm, setErrors, setFieldError } =
  useForm<ActivityTypeCreateInput>({
    validationSchema: toTypedSchema(activityTypeCreateSchema),
    initialValues: toFormValues(null),
  })

const [key] = defineField('key')
const [name] = defineField('name')
const [retentionDays] = defineField('retention_days')
const [exposePayloadToAgent] = defineField('expose_payload_to_agent')
const [echoIncludesContent] = defineField('echo_includes_content')
const [payloadSchema] = defineField('payload_schema')
const [validatorKind] = defineField('validator_kind')
const [webhookUrl] = defineField('webhook_url')
const [mcpAgentId] = defineField('mcp_agent_id')
const [mcpBindingId] = defineField('mcp_binding_id')
const [mcpToolName] = defineField('mcp_tool_name')
const [inProcessValidatorId] = defineField('in_process_validator_id')
const [exactMatchField] = defineField('exact_match_field')
const [exactMatchExpected] = defineField('exact_match_expected')
const [exactMatchCaseSensitive] = defineField('exact_match_case_sensitive')

// i18n key of a raw-JSON parse error surfaced by PayloadSchemaField, or null.
// When set, it takes precedence over the field's own `schemaEmpty` message so a
// malformed raw value reads as a parse error rather than an empty schema.
const schemaParseError = ref<string | null>(null)
const payloadSchemaError = computed(() => {
  if (schemaParseError.value) return t(`activities.typeForm.${schemaParseError.value}`)
  return errors.value.payload_schema ? t('activities.typeForm.schemaEmpty') : ''
})

// SInput's model accepts `string | number` (not null); retention is nullable
// (blank = no cap). Bridge to '' for display; the field keeps storing number | null.
const retentionDisplay = computed<string | number>({
  get: () => retentionDays.value ?? '',
  set: (v) => {
    retentionDays.value = v === '' || v === null ? null : Number(v)
  },
})

// First-party in-process validators are process-global; fetch them so the form can
// (a) gate the `in_process` kind on ≥1 being registered and (b) populate the picker.
const validatorsQuery = useQuery({
  queryKey: activityKeys.validators(),
  queryFn: () => listActivityValidators(),
})

const hasInProcessValidators = computed(() => (validatorsQuery.data.value ?? []).length > 0)

const validatorKindOptions = computed(() =>
  VALIDATOR_KINDS.filter((k) => k !== 'in_process' || hasInProcessValidators.value).map((k) => ({
    value: k,
    label: t(`activities.typeForm.validatorKind.${k}`),
  })),
)

const validatorOptions = computed(() =>
  (validatorsQuery.data.value ?? []).map((v) => ({ value: v.id, label: v.title })),
)

const isExactMatch = computed(
  () => validatorKind.value === 'in_process' && inProcessValidatorId.value === EXACT_MATCH_VALIDATOR_ID,
)

// The payload schema's property names — the fields an exact_match validator can
// compare against. Sourced from the same schema the builder is editing.
const schemaFieldOptions = computed(() => {
  const properties = (payloadSchema.value?.properties ?? {}) as Record<string, unknown>
  return Object.keys(properties).map((name) => ({ value: name, label: name }))
})

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

// Switching agents invalidates any binding chosen under the previous agent — but
// not during edit pre-fill, where agent and binding are hydrated together.
const hydrating = ref(false)
watch(mcpAgentId, () => {
  if (!hydrating.value) mcpBindingId.value = ''
})

// Sync the form to the target row each time the modal opens (create -> empty
// defaults, edit -> the row's disassembled fields). SchemaBuilder is remounted
// via :key so it re-seeds from the same row.
watch(
  () => props.open,
  (open) => {
    if (!open) return
    hydrating.value = true
    schemaParseError.value = null
    resetForm({ values: toFormValues(props.editType ?? null) })
    void nextTick(() => {
      hydrating.value = false
    })
  },
  { immediate: true },
)

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

const updateMutation = useMutation({
  mutationFn: (body: ActivityTypeUpdateIn) =>
    updateActivityType(props.projectId, props.editType!.id, body),
  onSuccess: () => {
    resetForm()
    emit('updated')
  },
  onError: (err) => {
    if (applyServerErrors(err)) return
    // In edit mode a 409 means the type is active (a behavioral edit was blocked),
    // not a key conflict — surface the distinct reason.
    if (err instanceof ApiError && err.status === 409) {
      toast.error(t('activities.typeForm.activeConflict'))
      return
    }
    if (err instanceof ApiError && err.status === 422) {
      toast.error(t('activities.typeForm.configRejected'))
      return
    }
    toast.error(t('activities.typeForm.updateFailed'))
  },
})

const isPending = computed(
  () => createMutation.isPending.value || updateMutation.isPending.value,
)

const validatorHint = computed(() => {
  if (validatorKind.value === 'webhook') return t('activities.typeForm.webhookHint')
  if (validatorKind.value === 'mcp') return t('activities.typeForm.mcpHint')
  return t('activities.typeForm.inProcessHint')
})

const onSubmit = handleSubmit((formValues) => {
  const shared = {
    name: formValues.name,
    payload_schema: formValues.payload_schema,
    validator_kind: formValues.validator_kind as ValidatorKind,
    validator_config: assembleValidatorConfig(formValues),
    retention_days: formValues.retention_days,
    expose_payload_to_agent: formValues.expose_payload_to_agent,
    echo_includes_content: formValues.echo_includes_content,
  }
  if (isEdit.value && props.editType) {
    updateMutation.mutate(shared)
    return
  }
  createMutation.mutate({ key: formValues.key, ...shared })
})

function onClose(): void {
  resetForm()
  emit('close')
}
</script>

<template>
  <SModal
    :open="props.open"
    :title="isEdit ? t('activities.typeForm.editTitle') : t('activities.typeForm.title')"
    size="lg"
    @close="onClose"
  >
    <form
      id="activity-type-form"
      class="flex flex-col gap-4"
      @submit.prevent="onSubmit"
    >
      <p class="text-sm text-[var(--color-muted)]">
        {{ t('activities.typeForm.intro') }}
      </p>

      <SFormField
        :label="t('activities.typeForm.key')"
        name="key"
        :error="errors.key ?? ''"
        :help="t('activities.typeForm.keyHelp')"
        required
      >
        <SInput
          v-model="key"
          :placeholder="t('activities.typeForm.keyPlaceholder')"
          :error="!!errors.key"
          :disabled="isEdit"
          data-testid="type-key"
        />
      </SFormField>

      <SFormField
        :label="t('activities.typeForm.name')"
        name="name"
        :error="errors.name ?? ''"
        :help="t('activities.typeForm.nameHelp')"
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
        :label="t('activities.typeForm.exposePayloadToAgent')"
        name="expose_payload_to_agent"
        :help="t('activities.typeForm.exposePayloadToAgentHelp')"
      >
        <SCheckbox
          v-model="exposePayloadToAgent"
          data-testid="type-expose-payload-to-agent"
        >
          {{ t('activities.typeForm.exposePayloadToAgentLabel') }}
        </SCheckbox>
      </SFormField>

      <SFormField
        :label="t('activities.typeForm.echoIncludesContent')"
        name="echo_includes_content"
        :help="t('activities.typeForm.echoIncludesContentHelp')"
      >
        <SCheckbox
          v-model="echoIncludesContent"
          data-testid="type-echo-includes-content"
        >
          {{ t('activities.typeForm.echoIncludesContentLabel') }}
        </SCheckbox>
      </SFormField>

      <SFormField
        :label="t('activities.typeForm.payloadSchema')"
        name="payload_schema"
        :error="payloadSchemaError"
        :help="t('activities.typeForm.payloadSchemaHelp')"
        required
      >
        <PayloadSchemaField
          :key="`${props.editType?.id ?? 'new'}:${String(props.open)}`"
          :initial="props.editType?.payload_schema ?? null"
          @update:model-value="(s) => (payloadSchema = s)"
          @update:parse-error="(e) => (schemaParseError = e)"
        />
      </SFormField>

      <SFormField
        :label="t('activities.typeForm.validator')"
        name="validator_kind"
        :error="errors.validator_kind ?? ''"
        :help="validatorHint"
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
        :help="t('activities.typeForm.webhookUrlHelp')"
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
          :help="t('activities.typeForm.mcpAgentHelp')"
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
          :help="!mcpAgentId
            ? t('activities.typeForm.mcpBindingSelectAgentFirst')
            : t('activities.typeForm.mcpBindingHelp')"
          required
        >
          <SSelect
            v-model="mcpBindingId"
            :options="bindingOptions"
            :disabled="!mcpAgentId"
            :placeholder="t('activities.typeForm.mcpBindingPlaceholder')"
          />
        </SFormField>

        <SFormField
          :label="t('activities.typeForm.mcpToolName')"
          name="mcp_tool_name"
          :error="errors.mcp_tool_name ? t('activities.typeForm.fieldRequired') : ''"
          :help="t('activities.typeForm.mcpToolNameHelp')"
          required
        >
          <SInput
            v-model="mcpToolName"
            :placeholder="t('activities.typeForm.mcpToolNamePlaceholder')"
            :error="!!errors.mcp_tool_name"
          />
        </SFormField>
      </template>

      <template v-if="validatorKind === 'in_process'">
        <SFormField
          :label="t('activities.typeForm.inProcessValidator')"
          name="in_process_validator_id"
          :error="errors.in_process_validator_id ? t('activities.typeForm.fieldRequired') : ''"
          :help="t('activities.typeForm.inProcessValidatorHelp')"
          required
        >
          <SSelect
            v-model="inProcessValidatorId"
            :options="validatorOptions"
            :placeholder="t('activities.typeForm.inProcessValidatorPlaceholder')"
            data-testid="type-in-process-validator"
          />
        </SFormField>

        <template v-if="isExactMatch">
          <SFormField
            :label="t('activities.typeForm.exactMatchField')"
            name="exact_match_field"
            :error="errors.exact_match_field ? t('activities.typeForm.fieldRequired') : ''"
            :help="t('activities.typeForm.exactMatchFieldHelp')"
            required
          >
            <SSelect
              v-model="exactMatchField"
              :options="schemaFieldOptions"
              :placeholder="t('activities.typeForm.exactMatchFieldPlaceholder')"
              data-testid="type-exact-match-field"
            />
          </SFormField>

          <SFormField
            :label="t('activities.typeForm.exactMatchExpected')"
            name="exact_match_expected"
            :error="errors.exact_match_expected ? t('activities.typeForm.fieldRequired') : ''"
            :help="t('activities.typeForm.exactMatchExpectedHelp')"
            required
          >
            <SInput
              v-model="exactMatchExpected"
              :error="!!errors.exact_match_expected"
              data-testid="type-exact-match-expected"
            />
          </SFormField>

          <SFormField
            :label="t('activities.typeForm.exactMatchCaseSensitivity')"
            name="exact_match_case_sensitive"
          >
            <SCheckbox v-model="exactMatchCaseSensitive">
              {{ t('activities.typeForm.exactMatchCaseSensitive') }}
            </SCheckbox>
          </SFormField>
        </template>
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
          :loading="isPending"
        >
          {{ isEdit ? t('activities.typeForm.saveChanges') : t('activities.typeForm.submit') }}
        </SButton>
      </div>
    </template>
  </SModal>
</template>
