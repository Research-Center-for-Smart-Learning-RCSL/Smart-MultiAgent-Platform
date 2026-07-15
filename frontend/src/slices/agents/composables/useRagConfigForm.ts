import { computed, watch, type ComputedRef, type Ref } from 'vue'
import type { ApiKey } from '@slices/keys'
import type { RagConfigCreateInput } from '../types/schemas'
import { useChunkParamsForm } from './useChunkParamsForm'

interface RagConfigFormOptions {
  embedKeys: ComputedRef<ApiKey[]>
  rerankKeys: ComputedRef<ApiKey[]>
  embedKeyId: Ref<string>
  embedProvider: Ref<string>
  rerankEnabled: Ref<boolean>
  rerankKeyId: Ref<string | null>
  rerankProvider: Ref<string | null>
  rerankModel: Ref<string | null>
}

export function useRagConfigForm(opts: RagConfigFormOptions) {
  const {
    embedKeys, rerankKeys,
    embedKeyId, embedProvider,
    rerankEnabled, rerankKeyId, rerankProvider, rerankModel,
  } = opts

  const {
    chunkSizeTokens,
    chunkOverlapTokens,
    similarityThreshold,
    resetChunkDefaults,
    assembleChunkParams,
    loadChunkParams,
  } = useChunkParamsForm()

  const hasEmbedKeys = computed(() => embedKeys.value.length > 0)

  watch(embedKeyId, (id) => {
    const key = embedKeys.value.find((k) => k.id === id)
    if (key) embedProvider.value = key.provider as RagConfigCreateInput['embed_provider']
  })

  watch(
    embedKeys,
    (keys) => {
      if (keys.length && !embedKeyId.value) embedKeyId.value = keys[0]!.id
    },
    { immediate: true },
  )

  // F-19: two rerank options — BYO-key 'cohere' or the keyless bundled local
  // 'bge'. On a fresh enable, default to cohere when a rerank key exists, else to
  // the keyless local reranker so the toggle is usable without a Cohere key. Guard
  // on an empty provider so loading an existing config (resetForm sets both
  // rerank_enabled and the persisted provider) does not clobber the saved choice —
  // e.g. a stored 'bge' config in a project that also has a Cohere key.
  watch(rerankEnabled, (on) => {
    if (on) {
      if (!rerankProvider.value) {
        rerankProvider.value = rerankKeys.value.length ? 'cohere' : 'bge'
      }
    } else {
      rerankProvider.value = null
      rerankKeyId.value = null
      rerankModel.value = null
    }
  })

  // Reconcile the dependent fields when the provider changes: 'bge' is keyless
  // and pins the bundled model; 'cohere' seeds a default key.
  watch(rerankProvider, (provider) => {
    if (provider === 'bge') {
      rerankKeyId.value = null
      rerankModel.value = 'bge-reranker-v2-m3'
    } else if (provider === 'cohere') {
      if (rerankModel.value === 'bge-reranker-v2-m3') rerankModel.value = null
      if (!rerankKeyId.value && rerankKeys.value.length) {
        rerankKeyId.value = rerankKeys.value[0]!.id
      }
    }
  })

  function defaultEmbedKey(): void {
    if (embedKeys.value.length) embedKeyId.value = embedKeys.value[0]!.id
  }

  function keyLabel(k: ApiKey): string {
    return `${k.name} (${k.provider})`
  }

  const embedKeyOptions = computed(() =>
    embedKeys.value.map((k) => ({ value: k.id, label: keyLabel(k) })),
  )
  const rerankKeyOptions = computed(() =>
    rerankKeys.value.map((k) => ({ value: k.id, label: keyLabel(k) })),
  )

  return {
    chunkSizeTokens,
    chunkOverlapTokens,
    similarityThreshold,
    hasEmbedKeys,
    embedKeyOptions,
    rerankKeyOptions,
    resetChunkDefaults,
    defaultEmbedKey,
    assembleChunkParams,
    loadChunkParams,
  }
}
