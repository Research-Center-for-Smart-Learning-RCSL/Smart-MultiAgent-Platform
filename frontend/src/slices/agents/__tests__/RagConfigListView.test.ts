import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import RagConfigListView from '../views/RagConfigListView.vue'

const routes = [
  {
    path: '/projects/:projectId/rag-configs',
    name: 'agents.ragConfigs',
    component: RagConfigListView,
  },
  {
    path: '/projects/:projectId/rag-configs/:configId',
    name: 'agents.ragConfig',
    component: { template: '<div />' },
  },
]

function seedHandlers(opts: { embedKey?: boolean } = {}): void {
  server.use(
    http.get('/api/projects/proj_1/rag-configs', () =>
      HttpResponse.json([
        {
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
        },
      ]),
    ),
    http.get('/api/projects/proj_1/keys', () =>
      HttpResponse.json(
        opts.embedKey === false
          ? []
          : [
              {
                id: 'key_1',
                provider: 'openai',
                name: 'My OpenAI',
                masked_preview: 'sk-...abcd',
                test_status: 'ok',
                test_error: null,
                last_test_at: null,
                created_at: '2026-01-01T00:00:00Z',
              },
            ],
      ),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('RagConfigListView', () => {
  it('lists the project RAG configurations fetched from the backend', async () => {
    seedHandlers()
    const wrapper = await renderView(RagConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs',
    })
    await settle(wrapper)
    expect(wrapper.find('table.s-table').exists()).toBe(true)
    expect(wrapper.text()).toContain('Handbook')
  })

  it('enables the create button when an embedding-capable key exists', async () => {
    seedHandlers({ embedKey: true })
    const wrapper = await renderView(RagConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs',
    })
    await settle(wrapper)
    const createBtn = wrapper.find('button.s-btn--primary')
    expect(createBtn.exists()).toBe(true)
    expect(createBtn.attributes('disabled')).toBeUndefined()
  })

  it('disables creation when the project has no embedding key', async () => {
    seedHandlers({ embedKey: false })
    const wrapper = await renderView(RagConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs',
    })
    await settle(wrapper)
    expect(wrapper.find('button.s-btn--primary').attributes('disabled')).toBeDefined()
  })
})
