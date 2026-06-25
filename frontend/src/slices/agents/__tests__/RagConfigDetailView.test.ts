import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import RagConfigDetailView from '../views/RagConfigDetailView.vue'

const routes = [
  {
    path: '/projects/:projectId/rag-configs/:configId',
    name: 'agents.ragConfig',
    component: RagConfigDetailView,
  },
  {
    path: '/projects/:projectId/rag-configs',
    name: 'agents.ragConfigs',
    component: { template: '<div />' },
  },
]

function seedHandlers(): void {
  server.use(
    http.get('/api/rag-configs/cfg_1', () =>
      HttpResponse.json({
        id: 'cfg_1',
        project_id: 'proj_1',
        name: 'Handbook',
        chunk_strategy: 'fixed',
        chunk_params: { chunk_size_tokens: 512, chunk_overlap_tokens: 64 },
        embed_key_id: 'key_1',
        embed_provider: 'openai',
        embed_model: 'text-embedding-3-small',
        rerank_enabled: false,
        rerank_key_id: null,
        rerank_provider: null,
        rerank_model: null,
        top_k: 5,
        created_at: '2026-01-01T00:00:00Z',
      }),
    ),
    http.get('/api/rag-configs/cfg_1/documents', () =>
      HttpResponse.json([
        {
          id: 'doc_1',
          rag_config_id: 'cfg_1',
          filename: 'guide.pdf',
          mime: 'application/pdf',
          size_bytes: 2048,
          status: 'ready',
          scan_status: 'clean',
          uploaded_at: '2026-01-02T00:00:00Z',
        },
      ]),
    ),
    http.get('/api/projects/proj_1/keys', () => HttpResponse.json([])),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('RagConfigDetailView', () => {
  it('renders the config name and a Save action on the settings tab', async () => {
    seedHandlers()
    const wrapper = await renderView(RagConfigDetailView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs/cfg_1',
    })
    await settle(wrapper)
    expect(wrapper.text()).toContain('Handbook')
    // Settings tab is active by default and exposes a primary Save button.
    expect(wrapper.find('button.s-btn--primary').exists()).toBe(true)
  })

  it('lists the config documents with an upload control on the documents tab', async () => {
    seedHandlers()
    const wrapper = await renderView(RagConfigDetailView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs/cfg_1?tab=documents',
    })
    await settle(wrapper)
    expect(wrapper.find('input[type="file"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('guide.pdf')
  })
})
