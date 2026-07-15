import { describe, it, expect } from 'vitest'
import { computed, nextTick, ref, type Ref } from 'vue'
import type { ApiKey } from '@slices/keys'
import { useRagConfigForm } from '../useRagConfigForm'

// F-19: the rerank provider is 'cohere' (BYO key) or the keyless bundled 'bge'.
// The composable owns the defaulting + dependent-field reconciliation that the
// create/edit views' provider selector relies on (AC-5).

function setup(rerankKeys: Partial<ApiKey>[] = []) {
  const rerankEnabled = ref(false)
  const rerankKeyId = ref<string | null>(null)
  const rerankProvider = ref<string | null>(null)
  const rerankModel = ref<string | null>(null)
  useRagConfigForm({
    embedKeys: computed(() => []),
    rerankKeys: computed(() => rerankKeys as ApiKey[]),
    embedKeyId: ref('') as Ref<string>,
    embedProvider: ref('') as Ref<string>,
    rerankEnabled,
    rerankKeyId,
    rerankProvider,
    rerankModel,
  })
  return { rerankEnabled, rerankKeyId, rerankProvider, rerankModel }
}

const COHERE_KEY: Partial<ApiKey> = { id: 'key_cohere', provider: 'cohere', name: 'My Cohere' }

async function flush(): Promise<void> {
  // Chained watchers (enable -> provider -> key/model) settle over a couple ticks.
  await nextTick()
  await nextTick()
}

describe('useRagConfigForm rerank provider', () => {
  it('defaults to keyless bge when the project has no rerank key', async () => {
    const f = setup([])
    f.rerankEnabled.value = true
    await flush()
    expect(f.rerankProvider.value).toBe('bge')
    expect(f.rerankKeyId.value).toBeNull()
    expect(f.rerankModel.value).toBe('bge-reranker-v2-m3')
  })

  it('defaults to cohere and seeds the key when a rerank key exists', async () => {
    const f = setup([COHERE_KEY])
    f.rerankEnabled.value = true
    await flush()
    expect(f.rerankProvider.value).toBe('cohere')
    expect(f.rerankKeyId.value).toBe('key_cohere')
  })

  it('clears the key and pins the bundled model when switching to bge', async () => {
    const f = setup([COHERE_KEY])
    f.rerankEnabled.value = true
    await flush()
    // Now switch the provider to the keyless local reranker.
    f.rerankProvider.value = 'bge'
    await flush()
    expect(f.rerankKeyId.value).toBeNull()
    expect(f.rerankModel.value).toBe('bge-reranker-v2-m3')
  })

  it('resets provider/key/model when reranking is disabled', async () => {
    const f = setup([COHERE_KEY])
    f.rerankEnabled.value = true
    await flush()
    f.rerankEnabled.value = false
    await flush()
    expect(f.rerankProvider.value).toBeNull()
    expect(f.rerankKeyId.value).toBeNull()
    expect(f.rerankModel.value).toBeNull()
  })
})
