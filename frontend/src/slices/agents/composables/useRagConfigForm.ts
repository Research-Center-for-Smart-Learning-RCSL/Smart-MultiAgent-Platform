import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import type { ApiKey } from '@slices/keys'
import type { RagConfigCreateInput } from '../types/schemas'

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

const CHUNK_DEFAULTS = { size: 512, overlap: 64, similarity: 0.8 }

export function useRagConfigForm(opts: RagConfigFormOptions) {
  const {
    embedKeys, rerankKeys,
    embedKeyId, embedProvider,
    rerankEnabled, rerankKeyId, rerankProvider, rerankModel,
  } = opts

  const chunkSizeTokens = ref(CHUNK_DEFAULTS.size)
  const chunkOverlapTokens = ref(CHUNK_DEFAULTS.overlap)
  const similarityThreshold = ref(CHUNK_DEFAULTS.similarity)

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

  function resetChunkDefaults(): void {
    chunkSizeTokens.value = CHUNK_DEFAULTS.size
    chunkOverlapTokens.value = CHUNK_DEFAULTS.overlap
    similarityThreshold.value = CHUNK_DEFAULTS.similarity
  }

  function defaultEmbedKey(): void {
    if (embedKeys.value.length) embedKeyId.value = embedKeys.value[0]!.id
  }

  function assembleChunkParams(strategy: string): Record<string, unknown> {
    return strategy === 'fixed'
      ? { chunk_size_tokens: chunkSizeTokens.value, chunk_overlap_tokens: chunkOverlapTokens.value }
      : { similarity_threshold: similarityThreshold.value }
  }

  function loadChunkParams(params: Record<string, unknown>): void {
    chunkSizeTokens.value = (params.chunk_size_tokens as number) ?? CHUNK_DEFAULTS.size
    chunkOverlapTokens.value = (params.chunk_overlap_tokens as number) ?? CHUNK_DEFAULTS.overlap
    similarityThreshold.value = (params.similarity_threshold as number) ?? CHUNK_DEFAULTS.similarity
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
