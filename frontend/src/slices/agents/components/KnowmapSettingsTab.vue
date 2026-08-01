<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SCard, SFormField, SInput, SSelect } from '@shared/ui'

defineProps<{
  chunkParamsLocked: boolean
  embedModel: string | null
  embedProvider: string | null
  errors: Partial<Record<string, string | undefined>>
  hasKeyGroups: boolean
  keyGroupOptions: Array<{ label: string; value: string }>
}>()
const emit = defineEmits<{ submit: [] }>()
const name = defineModel<string>('name', { required: true })
const builderKeyGroupId = defineModel<string>('builderKeyGroupId', { required: true })
const chunkStrategy = defineModel<string>('chunkStrategy', { required: true })
const chunkSizeTokens = defineModel<number>('chunkSizeTokens', { required: true })
const chunkOverlapTokens = defineModel<number>('chunkOverlapTokens', { required: true })
const similarityThreshold = defineModel<number>('similarityThreshold', { required: true })
const { t } = useI18n()
</script>

<template>
  <form
    class="mt-6 space-y-6"
    @submit.prevent="emit('submit')"
  >
    <SCard>
      <h3 class="text-lg font-semibold mb-4">
        {{ t('agents.knowmapForm.name') }}
      </h3>
      <SFormField
        :label="t('agents.knowmapForm.name')"
        name="name"
        :error="errors.name ?? ''"
        required
      >
        <SInput
          v-model="name"
          :error="!!errors.name"
        />
      </SFormField>
    </SCard>
    <SCard>
      <h3 class="text-lg font-semibold mb-4">
        {{ t('agents.knowmapForm.builderKeyGroup') }}
      </h3>
      <SFormField
        :label="t('agents.knowmapForm.builderKeyGroup')"
        name="builder_key_group_id"
        :error="errors.builder_key_group_id ?? ''"
        required
      >
        <SSelect
          v-model="builderKeyGroupId"
          :options="keyGroupOptions"
          :placeholder="t('agents.knowmapForm.builderKeyGroupPlaceholder')"
          :disabled="!hasKeyGroups"
        />
      </SFormField>
      <p
        v-if="embedProvider && embedModel"
        class="text-sm text-[var(--color-muted)] mt-2"
      >
        {{ t('agents.knowmapForm.embedResolved', { provider: embedProvider, model: embedModel }) }}
      </p>
    </SCard>
    <SCard>
      <h3 class="text-lg font-semibold mb-4">
        {{ t('agents.ragForm.chunkStrategy') }}
      </h3>
      <SFormField
        :label="t('agents.ragForm.chunkStrategy')"
        name="chunk_strategy"
      >
        <SSelect
          v-model="chunkStrategy"
          :options="[
            { value: 'fixed', label: t('agents.ragForm.chunkFixed') },
            { value: 'semantic', label: t('agents.ragForm.chunkSemantic') },
          ]"
          disabled
        />
      </SFormField>
      <p class="text-sm text-[var(--color-muted)] mt-2">
        {{ t('agents.ragForm.immutableHint') }}
      </p>
      <template v-if="chunkStrategy === 'fixed'">
        <div class="grid grid-cols-2 gap-4 mt-4">
          <SFormField
            :label="t('agents.ragForm.chunkSize')"
            name="chunk_size_tokens"
          >
            <SInput
              v-model="chunkSizeTokens"
              type="number"
              :disabled="chunkParamsLocked"
            />
          </SFormField>
          <SFormField
            :label="t('agents.ragForm.chunkOverlap')"
            name="chunk_overlap_tokens"
          >
            <SInput
              v-model="chunkOverlapTokens"
              type="number"
              :disabled="chunkParamsLocked"
            />
          </SFormField>
        </div>
      </template>
      <SFormField
        v-else
        :label="t('agents.ragForm.similarityThreshold')"
        name="similarity_threshold"
        class="mt-4"
      >
        <SInput
          v-model="similarityThreshold"
          type="number"
          :disabled="chunkParamsLocked"
        />
      </SFormField>
      <p
        v-if="chunkParamsLocked"
        class="text-sm text-[var(--color-muted)] mt-4"
      >
        {{ t('agents.ragForm.chunkParamsImmutableHint') }}
      </p>
    </SCard>
  </form>
</template>
