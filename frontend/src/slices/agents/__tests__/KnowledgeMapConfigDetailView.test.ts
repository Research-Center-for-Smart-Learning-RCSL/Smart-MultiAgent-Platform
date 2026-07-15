// F-22: the Knowledge Map detail page must subscribe to build state
// unconditionally on load (not gated on an already-in-progress state) and
// re-subscribe on every upload/delete, so an automatic rebuild started while the
// page shows idle is reflected live. These tests spy the build-state engine's
// `watch` to assert the de-gated mount subscribe and the mutation re-subscribe.

import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Ref } from 'vue'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import type * as SharedComposables from '@shared/composables'
import KnowledgeMapConfigDetailView from '../views/KnowledgeMapConfigDetailView.vue'

// Spy the build-state engine so we can assert the view's watchBuild call sites
// directly, and drive `liveState` to check the badge reflects a live frame.
const socket = vi.hoisted(() => ({
  watch: vi.fn(),
  unwatch: vi.fn(),
  liveState: null as unknown as Ref<Record<string, string>>,
}))
vi.mock('../composables/useKnowmapSocket', async () => {
  const { ref } = await import('vue')
  socket.liveState = ref({})
  return {
    useKnowmapSocket: () => ({ liveState: socket.liveState, watch: socket.watch, unwatch: socket.unwatch }),
  }
})

// Auto-confirm the delete dialog; keep every other shared composable intact.
vi.mock('@shared/composables', async (importOriginal) => {
  const actual = await importOriginal<typeof SharedComposables>()
  return { ...actual, useConfirmDialog: () => ({ confirm: async () => true }) }
})

const routes = [
  {
    path: '/projects/:projectId/knowmap-configs/:configId',
    name: 'agents.knowmapConfig',
    component: KnowledgeMapConfigDetailView,
  },
  { path: '/projects/:projectId/knowmap-configs', name: 'agents.knowmapConfigs', component: { template: '<div />' } },
  {
    path: '/projects/:projectId/knowmap-configs/:configId/graph',
    name: 'agents.knowmapGraph',
    component: { template: '<div />' },
  },
]

function knowmapConfig(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'cfg_1',
    project_id: 'proj_1',
    name: 'Handbook Map',
    builder_key_group_id: 'kg_1',
    chunk_strategy: 'fixed',
    chunk_params: { chunk_size_tokens: 512, chunk_overlap_tokens: 64 },
    embed_provider: 'openai',
    embed_model: 'text-embedding-3-small',
    embed_dim: 1536,
    last_build_state: 'idle',
    last_build_at: '2026-01-02T00:00:00Z',
    last_build_error: null,
    created_at: '2026-01-01T00:00:00Z',
    deleted_at: null,
    ...over,
  }
}

function doc(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'doc_1',
    knowmap_config_id: 'cfg_1',
    filename: 'guide.pdf',
    mime: 'application/pdf',
    size_bytes: 2048,
    status: 'ready',
    scan_status: 'clean',
    uploaded_at: '2026-01-02T00:00:00Z',
    agent_ids: [],
    ...over,
  }
}

function seed(opts: { config?: Record<string, unknown>; docs?: unknown[]; role?: 'owner' | 'member' } = {}): void {
  server.use(
    http.get('/api/knowmap-configs/cfg_1', () => HttpResponse.json(opts.config ?? knowmapConfig())),
    http.get('/api/knowmap-configs/cfg_1/documents', () => HttpResponse.json(opts.docs ?? [doc()])),
    http.get('/api/projects/proj_1/key-groups', () => HttpResponse.json([])),
    http.get('/api/projects/proj_1/agents', () => HttpResponse.json([])),
    http.get('/api/projects/proj_1/members', () =>
      HttpResponse.json([{ user_id: 'u_1', email: 'u@smap.test', role: opts.role ?? 'owner', joined_at: '2026-01-01T00:00:00Z' }]),
    ),
    http.delete('/api/knowmap-documents/doc_1', () => new HttpResponse(null, { status: 204 })),
  )
}

function signInOwner(): void {
  const session = useSessionStore()
  session.me = { id: 'u_1', email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 160))
  await wrapper.vm.$nextTick()
}

afterEach(() => {
  vi.clearAllMocks()
  if (socket.liveState) socket.liveState.value = {}
})

describe('KnowledgeMapConfigDetailView build-state visibility (F-22)', () => {
  it('subscribes to build state on load even when the config is idle, and reflects a live frame', async () => {
    seed({ config: knowmapConfig({ last_build_state: 'idle' }) })
    const wrapper = await renderView(KnowledgeMapConfigDetailView, {
      routes,
      initialRoute: '/projects/proj_1/knowmap-configs/cfg_1',
    })
    signInOwner()
    await settle(wrapper)

    // De-gated mount subscribe: today's in-progress guard would skip an idle config.
    expect(socket.watch).toHaveBeenCalledWith('cfg_1', 'idle')

    // A live `running` frame (auto-build) now drives the badge because the
    // channel was subscribed on load.
    socket.liveState.value = { cfg_1: 'running' }
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('agents.graphragList.states.running')
  })

  it('re-subscribes to build state on a document delete (covers every subsequent auto-build)', async () => {
    seed({ config: knowmapConfig({ last_build_state: 'idle' }), docs: [doc()], role: 'owner' })
    const wrapper = await renderView(KnowledgeMapConfigDetailView, {
      routes,
      initialRoute: '/projects/proj_1/knowmap-configs/cfg_1?tab=documents',
    })
    signInOwner()
    await settle(wrapper)
    const callsAfterMount = socket.watch.mock.calls.length
    expect(callsAfterMount).toBeGreaterThan(0)

    // Click the row's trash action; the mocked confirm resolves true.
    const deleteBtn = wrapper
      .findAll('button')
      .find((b) => b.find('svg').exists() && b.classes().some((c) => c.includes('icon')))
    expect(deleteBtn, 'a document-row delete button should render for an owner').toBeTruthy()
    await deleteBtn!.trigger('click')
    await settle(wrapper)

    // The delete success handler re-opens the build channel so the auto-build's
    // `running` frame reaches a live subscriber (F-22 §7.2).
    expect(socket.watch.mock.calls.length).toBeGreaterThan(callsAfterMount)
  })
})
