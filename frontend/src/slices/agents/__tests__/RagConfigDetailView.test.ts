import { afterEach, describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import type * as SharedComposables from '@shared/composables'
import { SFileUpload } from '@shared/ui'
import RagConfigDetailView from '../views/RagConfigDetailView.vue'

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('@shared/composables', async (importOriginal) => {
  const actual = await importOriginal<typeof SharedComposables>()
  return { ...actual, useToast: () => toast }
})

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
          agent_ids: ['agent_1'],
        },
      ]),
    ),
    http.get('/api/projects/proj_1/keys', () => HttpResponse.json([])),
    http.get('/api/projects/proj_1/agents', () =>
      HttpResponse.json([
        {
          id: 'agent_1',
          project_id: 'proj_1',
          name: 'Support Bot',
          model_hint: 'claude',
          model_id: null,
          key_group_id: 'kg_1',
          system_prompt: '',
          rag_config_id: 'cfg_1',
          context_mode: 'window',
          context_token_cap: null,
          a2a_enabled: false,
          wakeup_config: {},
          workflow_capabilities: {},
          version: 1,
          created_at: '2026-01-01T00:00:00Z',
          deleted_at: null,
        },
      ]),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

afterEach(() => {
  vi.clearAllMocks()
})

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

  it('shows the per-agent allowlist picker and the document agent count', async () => {
    seedHandlers()
    const wrapper = await renderView(RagConfigDetailView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs/cfg_1?tab=documents',
    })
    await settle(wrapper)
    // The bound agent appears in the upload allowlist picker (data, not i18n).
    expect(wrapper.text()).toContain('Support Bot')
    // The allowlist checkbox picker rendered.
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(true)
  })

  it('shows an actionable error when a duplicate has a different allowlist', async () => {
    seedHandlers()
    server.use(
      http.post('/api/rag-configs/cfg_1/documents', () =>
        HttpResponse.json(
          {
            type: 'https://smap.local/problems/knowledge/document-allowlist-conflict',
            title: 'Document allowlist differs',
            status: 409,
            detail:
              'document doc_1 already exists with a different agent allowlist; use PATCH /api/rag-documents/doc_1/agents',
          },
          { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    )
    const wrapper = await renderView(RagConfigDetailView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs/cfg_1?tab=documents',
    })
    await settle(wrapper)

    wrapper.findComponent(SFileUpload).vm.$emit('files', [
      new File(['same bytes'], 'guide.pdf', { type: 'application/pdf' }),
    ])
    await settle(wrapper)

    expect(toast.error).toHaveBeenCalledWith('agents.rag.uploadAllowlistConflict')
  })

  it.each([
    ['conflict', 409, 'agents.rag.uploadAllowlistConflict'],
    ['unprocessable', 422, 'agents.rag.uploadUnprocessable'],
    ['network', 0, 'agents.rag.uploadFailed'],
  ])(
    'reconciles the first accepted file when the second upload fails with %s',
    async (_kind, status, expectedToast) => {
      seedHandlers()
      let uploads = 0
      let documentReads = 0
      server.use(
        http.get('/api/rag-configs/cfg_1/documents', () => {
          documentReads += 1
          return HttpResponse.json([])
        }),
        http.post('/api/rag-configs/cfg_1/documents', () => {
          uploads += 1
          if (uploads === 1) {
            return HttpResponse.json({
              id: 'accepted',
              rag_config_id: 'cfg_1',
              filename: 'first.txt',
              mime: 'text/plain',
              size_bytes: 5,
              status: 'ingesting',
              scan_status: 'pending',
              failure_code: null,
              uploaded_at: '2026-01-02T00:00:00Z',
              agent_ids: [],
            })
          }
          if (status === 0) return HttpResponse.error()
          const slug =
            status === 409 ? 'document-allowlist-conflict' : 'document-unprocessable'
          return HttpResponse.json(
            {
              type: `https://smap.local/problems/knowledge/${slug}`,
              title: 'upload failed',
              status,
            },
            { status, headers: { 'Content-Type': 'application/problem+json' } },
          )
        }),
      )
      const wrapper = await renderView(RagConfigDetailView, {
        routes,
        initialRoute: '/projects/proj_1/rag-configs/cfg_1?tab=documents',
      })
      await settle(wrapper)

      wrapper.findComponent(SFileUpload).vm.$emit('files', [
        new File(['first'], 'first.txt', { type: 'text/plain' }),
        new File(['second'], 'second.txt', { type: 'text/plain' }),
      ])
      await settle(wrapper)

      expect(uploads).toBe(2)
      expect(documentReads).toBeGreaterThan(1)
      expect(toast.error).toHaveBeenCalledWith(expectedToast)
      expect(toast.success).not.toHaveBeenCalledWith('agents.rag.uploadStarted')
    },
  )

  // F-20 (R10.04): chunk params are fixed once documents exist. The detail view
  // disables the fixed-strategy chunk-size / overlap inputs and shows the
  // immutability hint when the documents query is non-empty; the create form
  // (empty corpus) leaves them editable.
  it('disables the chunk-param inputs and shows the hint when documents exist', async () => {
    seedHandlers() // default seed has one ready document
    const wrapper = await renderView(RagConfigDetailView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs/cfg_1',
    })
    await settle(wrapper)
    // Only the two fixed-strategy chunk-param number inputs are disabled; top_k
    // stays editable.
    expect(wrapper.findAll('input[type="number"]:disabled').length).toBe(2)
    // The test i18n runtime renders raw keys, so assert on the key.
    expect(wrapper.text()).toContain('agents.ragForm.chunkParamsImmutableHint')
  })

  it('leaves the chunk-param inputs editable when the config has no documents', async () => {
    seedHandlers()
    server.use(
      http.get('/api/rag-configs/cfg_1/documents', () => HttpResponse.json([])),
    )
    const wrapper = await renderView(RagConfigDetailView, {
      routes,
      initialRoute: '/projects/proj_1/rag-configs/cfg_1',
    })
    await settle(wrapper)
    expect(wrapper.findAll('input[type="number"]:disabled').length).toBe(0)
    expect(wrapper.text()).not.toContain('agents.ragForm.chunkParamsImmutableHint')
  })
})
