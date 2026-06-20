<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { FormField } from '@shared/ui'
import type { KeyGroup } from '@slices/keys'

interface RagConfigOption {
  id: string
  name: string
}

defineProps<{
  name: string
  modelHint: string
  modelId: string | null
  keyGroupId: string
  systemPrompt: string
  promptStrategy: string
  contextMode: string
  ragConfigId: string | null
  a2aEnabled: boolean
  errors: Record<string, string | undefined>
  keyGroups: KeyGroup[]
  ragConfigs: RagConfigOption[]
  textareaRows?: number
}>()

const emit = defineEmits<{
  'update:name': [value: string]
  'update:modelHint': [value: string]
  'update:modelId': [value: string | null]
  'update:keyGroupId': [value: string]
  'update:systemPrompt': [value: string]
  'update:promptStrategy': [value: string]
  'update:contextMode': [value: string]
  'update:ragConfigId': [value: string | null]
  'update:a2aEnabled': [value: boolean]
}>()

const { t } = useI18n()
</script>

<template>
  <FormField
    :label="t('agents.form.name')"
    name="name"
    :error="errors.name"
    required
  >
    <input
      id="name"
      :value="name"
      :aria-describedby="errors.name ? 'name-error' : undefined"
      :aria-invalid="!!errors.name"
      @input="emit('update:name', ($event.target as HTMLInputElement).value)"
    >
  </FormField>

  <FormField
    :label="t('agents.form.modelHint')"
    name="model_hint"
    :error="errors.model_hint"
    required
  >
    <select
      id="model_hint"
      :value="modelHint"
      @change="emit('update:modelHint', ($event.target as HTMLSelectElement).value)"
    >
      <option value="claude">
        Claude
      </option>
      <option value="openai">
        OpenAI
      </option>
      <option value="gemini">
        Gemini
      </option>
    </select>
  </FormField>

  <FormField
    :label="t('agents.form.modelId')"
    name="model_id"
    :error="errors.model_id"
  >
    <input
      id="model_id"
      :value="modelId ?? ''"
      :placeholder="t('agents.form.modelIdPlaceholder')"
      @input="emit('update:modelId', ($event.target as HTMLInputElement).value || null)"
    >
  </FormField>

  <FormField
    :label="t('agents.form.keyGroup')"
    name="key_group_id"
    :error="errors.key_group_id"
    required
  >
    <select
      id="key_group_id"
      :value="keyGroupId"
      @change="emit('update:keyGroupId', ($event.target as HTMLSelectElement).value)"
    >
      <option
        value=""
        disabled
      >
        {{ t('agents.form.keyGroupPlaceholder') }}
      </option>
      <option
        v-for="g in keyGroups"
        :key="g.id"
        :value="g.id"
      >
        {{ g.name }}
      </option>
    </select>
  </FormField>

  <FormField
    :label="t('agents.form.systemPrompt')"
    name="system_prompt"
    :error="errors.system_prompt"
  >
    <textarea
      id="system_prompt"
      :value="systemPrompt"
      :rows="textareaRows ?? 4"
      @input="emit('update:systemPrompt', ($event.target as HTMLTextAreaElement).value)"
    />
  </FormField>

  <FormField
    :label="t('agents.form.promptStrategy')"
    name="prompt_strategy"
    :error="errors.prompt_strategy"
  >
    <select
      id="prompt_strategy"
      :value="promptStrategy"
      @change="emit('update:promptStrategy', ($event.target as HTMLSelectElement).value)"
    >
      <option value="full">
        {{ t('agents.form.promptStrategyFull') }}
      </option>
      <option value="lazy">
        {{ t('agents.form.promptStrategyLazy') }}
      </option>
    </select>
  </FormField>

  <FormField
    :label="t('agents.form.contextMode')"
    name="context_mode"
    :error="errors.context_mode"
  >
    <select
      id="context_mode"
      :value="contextMode"
      @change="emit('update:contextMode', ($event.target as HTMLSelectElement).value)"
    >
      <option value="general">
        {{ t('agents.form.contextModeGeneral') }}
      </option>
      <option value="compact">
        {{ t('agents.form.contextModeCompact') }}
      </option>
    </select>
  </FormField>

  <FormField
    :label="t('agents.form.ragConfig')"
    name="rag_config_id"
    :error="errors.rag_config_id"
  >
    <select
      id="rag_config_id"
      :value="ragConfigId ?? ''"
      @change="emit('update:ragConfigId', ($event.target as HTMLSelectElement).value || null)"
    >
      <option value="">
        {{ t('agents.form.ragConfigNone') }}
      </option>
      <option
        v-for="rc in ragConfigs"
        :key="rc.id"
        :value="rc.id"
      >
        {{ rc.name }}
      </option>
    </select>
    <slot name="after-rag" />
  </FormField>

  <slot name="extra-fields" />

  <FormField
    :label="t('agents.form.a2aEnabled')"
    name="a2a_enabled"
    :error="errors.a2a_enabled"
  >
    <input
      id="a2a_enabled"
      :checked="a2aEnabled"
      type="checkbox"
      @change="emit('update:a2aEnabled', ($event.target as HTMLInputElement).checked)"
    >
  </FormField>
</template>
