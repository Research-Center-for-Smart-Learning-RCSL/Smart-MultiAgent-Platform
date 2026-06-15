<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { ElMessageBox } from 'element-plus'

import { FormField } from '@shared/ui'
import { useServerErrors, useToast } from '@shared/composables'
import { projectKeysApi, CAPABILITIES, keysKeys, type ApiKey } from '@slices/keys'
import { agentsApi, type RagConfig } from '../api'
import { agentKeys } from '../queries'
import { ragConfigCreateSchema, type RagConfigCreateInput } from '../types/schemas'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const projectId = route.params.projectId as string
const toast = useToast()

const showForm = ref(false)

const configsQuery = useQuery({
  queryKey: agentKeys.ragConfigs(projectId),
  queryFn: async () => (await agentsApi.listRagConfigs(projectId)).data,
})

// Carried project keys are the source for the embed / rerank pickers; a RAG
// config cannot be built without an embedding key, so we block submit when the
// project has none of the right capability.
const projectKeysQuery = useQuery({
  queryKey: keysKeys.projectKeys(projectId),
  queryFn: async () => (await projectKeysApi.listCarried(projectId)).data,
})

const embedKeys = computed(() =>
  (projectKeysQuery.data.value ?? []).filter((k) =>
    CAPABILITIES[k.provider].includes('embedding'),
  ),
)
const rerankKeys = computed(() =>
  (projectKeysQuery.data.value ?? []).filter((k) =>
    CAPABILITIES[k.provider].includes('rerank'),
  ),
)
const hasEmbedKeys = computed(() => embedKeys.value.length > 0)
const hasRerankKeys = computed(() => rerankKeys.value.length > 0)

const schema = toTypedSchema(ragConfigCreateSchema)
const { handleSubmit, errors, defineField, resetForm, setErrors, values } =
  useForm<RagConfigCreateInput>({
    validationSchema: schema,
    initialValues: {
      name: '',
      chunk_strategy: 'fixed',
      chunk_params: {},
      embed_key_id: '',
      embed_provider: 'openai',
      embed_model: '',
      rerank_enabled: false,
      rerank_key_id: null,
      rerank_provider: null,
      rerank_model: null,
      top_k: 8,
    },
  })

const [name] = defineField('name')
const [chunkStrategy] = defineField('chunk_strategy')
const [embedKeyId] = defineField('embed_key_id')
const [embedProvider] = defineField('embed_provider')
const [embedModel] = defineField('embed_model')
const [rerankEnabled] = defineField('rerank_enabled')
const [rerankKeyId] = defineField('rerank_key_id')
const [rerankModel] = defineField('rerank_model')
const [topK] = defineField('top_k')

// Chunk params are a free-form JSONB blob on the backend; expose the few keys
// the chunkers actually read (knowledge/infrastructure/chunkers.py) and assemble
// them at submit. Defaults mirror DEFAULT_CHUNK_PARAMS so an untouched form is
// valid. These live outside vee-validate, so resetCreateForm() must reset them
// explicitly (resetForm only touches registered fields).
const CHUNK_DEFAULTS = { size: 512, overlap: 64, similarity: 0.8 }
const chunkSizeTokens = ref(CHUNK_DEFAULTS.size)
const chunkOverlapTokens = ref(CHUNK_DEFAULTS.overlap)
const similarityThreshold = ref(CHUNK_DEFAULTS.similarity)

// embed_provider is determined by the chosen key, never picked independently —
// keep them in lockstep so the backend never sees a provider/key mismatch.
watch(embedKeyId, (id) => {
  const key = embedKeys.value.find((k) => k.id === id)
  if (key) embedProvider.value = key.provider as RagConfigCreateInput['embed_provider']
})

// Default the embed picker to the first capable key once loaded.
watch(
  embedKeys,
  (keys) => {
    if (keys.length && !embedKeyId.value) embedKeyId.value = keys[0]!.id
  },
  { immediate: true },
)

// rerank_provider is fixed to cohere when enabled (cohere is the only rerank
// provider, R7.01); it is never picked directly, so manage it via the toggle.
const [rerankProvider] = defineField('rerank_provider')

// Rerank is opt-in; toggling it owns the dependent fields so a disabled rerank
// never ships a stale key/model.
watch(rerankEnabled, (on) => {
  if (on) {
    rerankProvider.value = 'cohere'
    if (!rerankKeyId.value && rerankKeys.value.length) {
      rerankKeyId.value = rerankKeys.value[0]!.id
    }
  } else {
    rerankProvider.value = null
    rerankKeyId.value = null
    rerankModel.value = null
  }
})

// Rerank requires a cohere key; if the toggle is on without one the backend
// rejects with CapabilityMismatch, so block submit client-side.
const rerankIncomplete = computed(() => rerankEnabled.value && !rerankKeyId.value)

// resetForm only resets registered vee-validate fields, so the standalone chunk
// refs and the auto-defaulted embed key must be reset by hand — otherwise the
// next "New Configuration" inherits the previous config's chunk sizes and shows
// an empty embed-key picker.
function resetCreateForm(): void {
  resetForm()
  chunkSizeTokens.value = CHUNK_DEFAULTS.size
  chunkOverlapTokens.value = CHUNK_DEFAULTS.overlap
  similarityThreshold.value = CHUNK_DEFAULTS.similarity
  if (embedKeys.value.length) embedKeyId.value = embedKeys.value[0]!.id
}

const { applyServerErrors } = useServerErrors(setErrors)

const createMutation = useMutation({
  mutationFn: async (payload: RagConfigCreateInput) =>
    (await agentsApi.createRagConfig(projectId, payload)).data,
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.ragConfigs(projectId) })
    resetCreateForm()
    showForm.value = false
    toast.success(t('agents.ragList.created'))
  },
  onError: (err) => {
    if (!applyServerErrors(err)) toast.error(t('agents.ragList.createFailed'))
  },
})

const onSubmit = handleSubmit((formValues) => {
  const chunk_params =
    formValues.chunk_strategy === 'fixed'
      ? {
          chunk_size_tokens: chunkSizeTokens.value,
          chunk_overlap_tokens: chunkOverlapTokens.value,
        }
      : { similarity_threshold: similarityThreshold.value }
  createMutation.mutate({ ...formValues, chunk_params })
})

const deleteMutation = useMutation({
  mutationFn: (id: string) => agentsApi.deleteRagConfig(id),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: agentKeys.ragConfigs(projectId) })
    toast.success(t('agents.ragList.deleted'))
  },
  onError: () => toast.error(t('agents.ragList.deleteFailed')),
})

async function confirmDelete(cfg: RagConfig): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('agents.ragList.deleteConfirm', { name: cfg.name }),
      t('agents.ragList.deleteTitle'),
      { type: 'warning' },
    )
  } catch {
    return // user dismissed
  }
  deleteMutation.mutate(cfg.id)
}

function keyLabel(k: ApiKey): string {
  return `${k.name} (${k.provider})`
}
</script>

<template>
  <section class="rag-list p-6">
    <div class="rag-list__header">
      <h1 class="text-xl font-semibold mb-4">
        {{ t('agents.ragList.title') }}
      </h1>
      <button
        class="btn btn-primary"
        :disabled="!hasEmbedKeys"
        @click="showForm = !showForm"
      >
        {{ showForm ? t('agents.ragList.cancel') : t('agents.ragList.create') }}
      </button>
    </div>

    <p
      v-if="!projectKeysQuery.isLoading.value && !hasEmbedKeys"
      class="rag-list__warning"
      role="alert"
    >
      {{ t('agents.ragList.noEmbedKeys') }}
    </p>

    <form
      v-if="showForm"
      class="rag-list__form"
      @submit.prevent="onSubmit"
    >
      <FormField
        :label="t('agents.ragForm.name')"
        name="name"
        :error="errors.name"
        required
      >
        <input
          id="name"
          v-model="name"
          :aria-invalid="!!errors.name"
        >
      </FormField>

      <FormField
        :label="t('agents.ragForm.embedKey')"
        name="embed_key_id"
        :error="errors.embed_key_id"
        required
      >
        <select
          id="embed_key_id"
          v-model="embedKeyId"
        >
          <option
            value=""
            disabled
          >
            {{ t('agents.ragForm.embedKeyPlaceholder') }}
          </option>
          <option
            v-for="k in embedKeys"
            :key="k.id"
            :value="k.id"
          >
            {{ keyLabel(k) }}
          </option>
        </select>
      </FormField>

      <FormField
        :label="t('agents.ragForm.embedModel')"
        name="embed_model"
        :error="errors.embed_model"
        required
      >
        <input
          id="embed_model"
          v-model="embedModel"
          :placeholder="t('agents.ragForm.embedModelHint')"
          :aria-invalid="!!errors.embed_model"
        >
      </FormField>

      <FormField
        :label="t('agents.ragForm.chunkStrategy')"
        name="chunk_strategy"
        :error="errors.chunk_strategy"
      >
        <select
          id="chunk_strategy"
          v-model="chunkStrategy"
        >
          <option value="fixed">
            {{ t('agents.ragForm.chunkFixed') }}
          </option>
          <option value="semantic">
            {{ t('agents.ragForm.chunkSemantic') }}
          </option>
        </select>
      </FormField>

      <template v-if="values.chunk_strategy === 'fixed'">
        <FormField
          :label="t('agents.ragForm.chunkSize')"
          name="chunk_size_tokens"
        >
          <input
            id="chunk_size_tokens"
            v-model.number="chunkSizeTokens"
            type="number"
            min="1"
          >
        </FormField>
        <FormField
          :label="t('agents.ragForm.chunkOverlap')"
          name="chunk_overlap_tokens"
        >
          <input
            id="chunk_overlap_tokens"
            v-model.number="chunkOverlapTokens"
            type="number"
            min="0"
          >
        </FormField>
      </template>
      <FormField
        v-else
        :label="t('agents.ragForm.similarityThreshold')"
        name="similarity_threshold"
      >
        <input
          id="similarity_threshold"
          v-model.number="similarityThreshold"
          type="number"
          min="0"
          max="1"
          step="0.05"
        >
      </FormField>

      <FormField
        :label="t('agents.ragForm.topK')"
        name="top_k"
        :error="errors.top_k"
      >
        <input
          id="top_k"
          v-model.number="topK"
          type="number"
          min="1"
          max="100"
        >
      </FormField>

      <FormField
        :label="t('agents.ragForm.rerankEnabled')"
        name="rerank_enabled"
        :error="errors.rerank_enabled"
      >
        <input
          id="rerank_enabled"
          v-model="rerankEnabled"
          type="checkbox"
          :disabled="!hasRerankKeys"
        >
        <span
          v-if="!hasRerankKeys"
          class="rag-list__hint"
        >{{ t('agents.ragForm.noRerankKeys') }}</span>
      </FormField>

      <template v-if="rerankEnabled">
        <FormField
          :label="t('agents.ragForm.rerankKey')"
          name="rerank_key_id"
          :error="errors.rerank_key_id"
        >
          <select
            id="rerank_key_id"
            v-model="rerankKeyId"
          >
            <option
              :value="null"
              disabled
            >
              {{ t('agents.ragForm.rerankKeyPlaceholder') }}
            </option>
            <option
              v-for="k in rerankKeys"
              :key="k.id"
              :value="k.id"
            >
              {{ keyLabel(k) }}
            </option>
          </select>
        </FormField>
        <FormField
          :label="t('agents.ragForm.rerankModel')"
          name="rerank_model"
          :error="errors.rerank_model"
        >
          <input
            id="rerank_model"
            v-model="rerankModel"
          >
        </FormField>
      </template>

      <button
        type="submit"
        class="btn btn-primary"
        :disabled="createMutation.isPending.value || !hasEmbedKeys || rerankIncomplete"
      >
        {{ t('agents.ragForm.submit') }}
      </button>
    </form>

    <p v-if="configsQuery.isLoading.value">
      {{ t('agents.ragList.loading') }}
    </p>
    <table
      v-else
      class="rag-list__table"
    >
      <thead>
        <tr>
          <th>{{ t('agents.ragList.colName') }}</th>
          <th>{{ t('agents.ragList.colEmbed') }}</th>
          <th>{{ t('agents.ragList.colActions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="c in configsQuery.data.value ?? []"
          :key="c.id"
        >
          <td>
            <RouterLink
              :to="{ name: 'agents.ragConfig', params: { projectId, configId: c.id } }"
            >
              {{ c.name }}
            </RouterLink>
          </td>
          <td>{{ c.embed_provider }} / {{ c.embed_model }}</td>
          <td>
            <button
              class="btn btn-danger"
              type="button"
              @click="confirmDelete(c)"
            >
              {{ t('agents.ragList.delete') }}
            </button>
          </td>
        </tr>
        <tr v-if="(configsQuery.data.value ?? []).length === 0">
          <td colspan="3">
            {{ t('agents.ragList.empty') }}
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.rag-list__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.rag-list__form {
  max-width: 480px;
  margin-bottom: var(--space-6);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.rag-list__warning {
  color: var(--color-danger, #b91c1c);
  margin-bottom: var(--space-3);
}
.rag-list__hint {
  display: block;
  color: var(--color-muted);
  font-size: 0.875rem;
  margin-top: var(--space-1);
}
.rag-list__table {
  width: 100%;
  border-collapse: collapse;
}
.rag-list__table th,
.rag-list__table td {
  text-align: left;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
</style>
