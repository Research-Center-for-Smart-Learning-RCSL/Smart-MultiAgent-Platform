import { ref } from 'vue'

// similarity is the semantic-chunk topic-shift threshold (cosine to the chunk
// centroid). Kept low to match the backend default — a high value over-fragments
// coherent prose into tiny chunks. See DEFAULT_SEMANTIC_CHUNK_PARAMS.
const CHUNK_DEFAULTS = { size: 512, overlap: 64, similarity: 0.3 }

// The chunk_strategy/chunk_params half of a document-corpus config form —
// shared by RAG (useRagConfigForm, which adds embed/rerank key selection on
// top) and Knowledge Map (which has no embed key selection: its embedding is
// resolved server-side from builder_key_group_id, same as GraphRAG).
export function useChunkParamsForm() {
  const chunkSizeTokens = ref(CHUNK_DEFAULTS.size)
  const chunkOverlapTokens = ref(CHUNK_DEFAULTS.overlap)
  const similarityThreshold = ref(CHUNK_DEFAULTS.similarity)

  function resetChunkDefaults(): void {
    chunkSizeTokens.value = CHUNK_DEFAULTS.size
    chunkOverlapTokens.value = CHUNK_DEFAULTS.overlap
    similarityThreshold.value = CHUNK_DEFAULTS.similarity
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

  return {
    chunkSizeTokens,
    chunkOverlapTokens,
    similarityThreshold,
    resetChunkDefaults,
    assembleChunkParams,
    loadChunkParams,
  }
}
