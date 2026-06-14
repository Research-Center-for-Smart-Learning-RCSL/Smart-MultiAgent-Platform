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
]

function seedHandlers(): void {
  server.use(
    http.get('/api/rag-configs/cfg_1', () =>
      HttpResponse.json({
        id: 'cfg_1',
        project_id: 'proj_1',
        name: 'Handbook',
        chunk_strategy: 'fixed',
        chunk_params: {},
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
  )
}

describe('RagConfigDetailView', () => {
  it('renders without errors', async () => {
    seedHandlers()
    const wrapper = await renderView(RagConfigDetailView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs/cfg_1',
    })
    expect(wrapper.exists()).toBe(true)
    // The upload control is always present (a labelled file input).
    expect(wrapper.find('input[type="file"]').exists()).toBe(true)
  })

  it('lists the config documents fetched from the backend', async () => {
    seedHandlers()
    const wrapper = await renderView(RagConfigDetailView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs/cfg_1',
    })
    await new Promise((r) => setTimeout(r, 100))
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('guide.pdf')
  })
})
