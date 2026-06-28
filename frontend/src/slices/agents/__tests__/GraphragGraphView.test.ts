import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'

// Stub Vue Flow: jsdom has no SVG layout, so the real component throws in
// fitView/getBBox during measurement. We only need to assert our own wiring
// (search box, container vs empty state), not Vue Flow's internals.
vi.mock('@vue-flow/core', () => ({
  VueFlow: { name: 'VueFlow', template: '<div class="vue-flow"><slot /></div>' },
}))
vi.mock('@vue-flow/background', () => ({ Background: { template: '<div />' } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { template: '<div />' } }))

import GraphragGraphView from '../views/GraphragGraphView.vue'

const routes = [
  {
    path: '/projects/:projectId/graphrag-configs/:configId/graph',
    name: 'agents.graphragGraph',
    component: GraphragGraphView,
  },
  {
    path: '/projects/:projectId/graphrag-configs',
    name: 'agents.graphragConfigs',
    component: { template: '<div />' },
  },
]

function seedGraph(body: unknown): void {
  server.use(http.get('/api/graphrag/gr_1/graph', () => HttpResponse.json(body)))
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

const ROUTE = '/projects/proj_1/graphrag-configs/gr_1/graph'

describe('GraphragGraphView', () => {
  it('renders the search box and a graph canvas when nodes exist', async () => {
    seedGraph({
      config_id: 'gr_1',
      nodes: [
        { id: 'alice', degree: 1, build_id: null, type: 'person' },
        { id: 'bob', degree: 1, build_id: null, type: 'organization' },
      ],
      edges: [{ source: 'alice', relation: 'knows', target: 'bob', confidence: 0.9 }],
      truncated: false,
    })
    const wrapper = await renderView(GraphragGraphView, { routes, initialRoute: ROUTE })
    await settle(wrapper)
    expect(wrapper.find('input').exists()).toBe(true)
    expect(wrapper.find('.vue-flow').exists()).toBe(true)
  })

  it('shows an empty state when the graph has no nodes', async () => {
    seedGraph({ config_id: 'gr_1', nodes: [], edges: [], truncated: false })
    const wrapper = await renderView(GraphragGraphView, { routes, initialRoute: ROUTE })
    await settle(wrapper)
    expect(wrapper.find('.vue-flow').exists()).toBe(false)
  })
})
