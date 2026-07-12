import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { defineComponent } from 'vue'
import { http, HttpResponse } from 'msw'

import { server } from '../../../../tests/mocks/server'
import { useModelCatalog, MODEL_CATALOG_KEY } from '../useModelCatalog'

// The full catalog body (chat + embedding); prompt-studio reads only `chat`,
// the agent/RAG forms read both. One shared query serves them all.
const CATALOG = {
  chat: [{ provider: 'openai', models: ['gpt-4o'], default: 'gpt-4o', context_limit: 128000 }],
  embedding: [
    {
      provider: 'openai',
      models: [{ model: 'text-embedding-3-small', dimension: 1536 }],
      default: 'text-embedding-3-small',
    },
  ],
}

// Mount a throwaway host that instantiates the composable inside a component
// setup, wired to the given QueryClient — the harness vue-query needs.
function mountCatalog(qc: QueryClient) {
  let api!: ReturnType<typeof useModelCatalog>
  const Host = defineComponent({
    setup() {
      api = useModelCatalog()
      return () => null
    },
  })
  const wrapper = mount(Host, {
    global: { plugins: [[VueQueryPlugin, { queryClient: qc }]] },
  })
  return { wrapper, get api() { return api } }
}

describe('useModelCatalog', () => {
  let hits = 0

  afterEach(() => {
    hits = 0
  })

  function serve(): void {
    server.use(
      http.get('/api/model-catalog', () => {
        hits++
        return HttpResponse.json(CATALOG)
      }),
    )
  }

  it('fetches the catalog and exposes the typed body', async () => {
    serve()
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const host = mountCatalog(qc)
    await flushPromises()
    expect(host.api.data.value).toEqual(CATALOG)
  })

  it('shares one cached fetch across independent consumers under a global key', async () => {
    serve()
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    // Two independent consumers (as the agents + prompt-studio slices are) reading
    // the same global key must trigger exactly one network fetch -- the dedup FU-5
    // delivers over the old per-slice query keys.
    mountCatalog(qc)
    mountCatalog(qc)
    await flushPromises()
    expect(hits).toBe(1)
    expect(qc.getQueryData(MODEL_CATALOG_KEY)).toEqual(CATALOG)
  })
})
