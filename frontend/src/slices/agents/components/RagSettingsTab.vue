<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SCard, SFormField, SInput, SSelect, SToggle } from '@shared/ui'

defineProps<{
  chunkParamsLocked: boolean
  embedKeyOptions: Array<{ label: string; value: string }>
  errors: Partial<Record<string, string | undefined>>
  rerankKeyOptions: Array<{ label: string; value: string }>
  rerankProviderOptions: Array<{ label: string; value: string }>
}>()
const emit = defineEmits<{ submit: [] }>()
const embedKeyId = defineModel<string>('embedKeyId', { required: true })
const embedModel = defineModel<string>('embedModel', { required: true })
const chunkStrategy = defineModel<string>('chunkStrategy', { required: true })
const chunkSizeTokens = defineModel<number>('chunkSizeTokens', { required: true })
const chunkOverlapTokens = defineModel<number>('chunkOverlapTokens', { required: true })
const similarityThreshold = defineModel<number>('similarityThreshold', { required: true })
const topK = defineModel<number>('topK', { required: true })
const rerankEnabled = defineModel<boolean>('rerankEnabled', { required: true })
const rerankProvider = defineModel<string | null>('rerankProvider', { required: true })
const rerankKeyId = defineModel<string | null>('rerankKeyId', { required: true })
const rerankModelDisplay = defineModel<string | number>('rerankModelDisplay', { required: true })
const { t } = useI18n()
</script>

<template>
  <form
    class="mt-6 space-y-6"
    @submit.prevent="emit('submit')"
  >
    <SCard>
      <h3 class="text-lg font-semibold mb-4">
        {{ t('agents.ragForm.embedProvider') }}
      </h3>
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SFormField
          :label="t('agents.ragForm.embedKey')"
          name="embed_key_id"
          :error="errors.embed_key_id ?? ''"
          required
        >
          <SSelect
            v-model="embedKeyId"
            :options="embedKeyOptions"
            :placeholder="t('agents.ragForm.embedKeyPlaceholder')"
            disabled
          />
        </SFormField>
        <SFormField
          :label="t('agents.ragForm.embedModel')"
          name="embed_model"
          :error="errors.embed_model ?? ''"
          required
        >
          <SInput
            v-model="embedModel"
            :placeholder="t('agents.ragForm.embedModelHint')"
            :error="!!errors.embed_model"
            disabled
          />
        </SFormField>
      </div>
      <p class="text-sm text-[var(--color-muted)] mt-2">
        {{ t('agents.ragForm.immutableHint') }}
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
    <SCard>
      <h3 class="text-lg font-semibold mb-4">
        {{ t('agents.ragForm.topK') }}
      </h3>
      <SFormField
        :label="t('agents.ragForm.topK')"
        name="top_k"
        :error="errors.top_k ?? ''"
      >
        <SInput
          v-model="topK"
          type="number"
        />
      </SFormField>
      <SFormField
        :label="t('agents.ragForm.rerankEnabled')"
        name="rerank_enabled"
        class="mt-4"
      >
        <SToggle
          v-model="rerankEnabled"
          variant="robot"
        />
      </SFormField>
      <template v-if="rerankEnabled">
        <SFormField
          :label="t('agents.ragForm.rerankProvider')"
          name="rerank_provider"
          class="mt-4"
        >
          <SSelect
            v-model="rerankProvider"
            :options="rerankProviderOptions"
          />
        </SFormField>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
          <SFormField
            v-if="rerankProvider === 'cohere'"
            :label="t('agents.ragForm.rerankKey')"
            name="rerank_key_id"
            :error="errors.rerank_key_id ?? ''"
          >
            <SSelect
              v-model="rerankKeyId"
              :options="rerankKeyOptions"
              :placeholder="t('agents.ragForm.rerankKeyPlaceholder')"
            />
          </SFormField>
          <SFormField
            :label="t('agents.ragForm.rerankModel')"
            name="rerank_model"
            :error="errors.rerank_model ?? ''"
          >
            <SInput v-model="rerankModelDisplay" />
          </SFormField>
        </div>
      </template>
    </SCard>
  </form>
</template>
