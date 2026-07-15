// F-22 sibling (§7.3): the Concept Map list had the same in-progress-gated
// per-row watchBuild, so an auto-build started elsewhere while a row shows idle
// was invisible until reload. This asserts each rendered row subscribes on
// render regardless of its initial state. Kept in a separate file from
// GraphragConfigListView.test.ts so the build-state engine can be mocked here
// without disturbing that file's data-rendering tests (which use the real one).

import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Ref } from 'vue'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import GraphragConfigListView from '../views/GraphragConfigListView.vue'

const socket = vi.hoisted(() => ({
  watch: vi.fn(),
  unwatch: vi.fn(),
  liveState: null as unknown as Ref<Record<string, string>>,
}))
vi.mock('../composables/useGraphragSocket', async () => {
  const { ref } = await import('vue')
  socket.liveState = ref({})
  return {
    useGraphragSocket: () => ({ liveState: socket.liveState, watch: socket.watch, unwatch: socket.unwatch }),
  }
})

const routes = [
  { path: '/projects/:projectId/graphrag-configs', name: 'agents.graphragConfigs', component: GraphragConfigListView },
  {
    path: '/projects/:projectId/graphrag-configs/:configId/graph',
    name: 'agents.graphragGraph',
    component: { template: '<div />' },
  },
]

function cfg(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'gr_1',
    project_id: 'proj_1',
    owner_kind: 'agent_group',
    owner_id: 'grp_1',
    owner_name: 'Research Group',
    agent_id: 'agent_1',
    builder_key_group_id: 'kg_2',
    trigger_config: {},
    recency_half_life_days: null,
    last_build_state: 'idle',
    last_build_at: '2026-01-02T00:00:00Z',
    last_build_error: null,
    created_at: '2026-01-01T00:00:00Z',
    deleted_at: null,
    ...over,
  }
}

function seed(configs: unknown[]): void {
  server.use(
    http.get('/api/projects/proj_1/graphrag-configs', () => HttpResponse.json(configs)),
    http.get('/api/projects/proj_1/graphrag-configs/owner-options', () => HttpResponse.json([])),
    http.get('/api/projects/proj_1/key-groups', () => HttpResponse.json([])),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 120))
  await wrapper.vm.$nextTick()
}

afterEach(() => {
  vi.clearAllMocks()
  if (socket.liveState) socket.liveState.value = {}
})

describe('GraphragConfigListView build-state visibility (F-22 sibling)', () => {
  it('subscribes each rendered row on render, even one that is idle', async () => {
    seed([cfg({ id: 'gr_1', last_build_state: 'idle' })])
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)

    // Today's in-progress guard would skip an idle row; the de-gate subscribes it.
    expect(socket.watch).toHaveBeenCalledWith('gr_1', 'idle')

    // A live `running` frame now drives the row badge because it is subscribed.
    socket.liveState.value = { gr_1: 'running' }
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('agents.graphragList.states.running')
  })
})
