import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import GraphragConfigListView from '../views/GraphragConfigListView.vue'

const routes = [
  {
    path: '/projects/:projectId/graphrag-configs',
    name: 'agents.graphragConfigs',
    component: GraphragConfigListView,
  },
  {
    path: '/projects/:projectId/graphrag-configs/:configId/graph',
    name: 'agents.graphragGraph',
    component: { template: '<div />' },
  },
]

const KEY_GROUPS = [
  { id: 'kg_1', project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
  { id: 'kg_2', project_id: 'proj_1', name: 'Builder', created_at: '2026-01-01T00:00:00Z' },
]

const OWNER_OPTIONS = [
  { owner_kind: 'chatroom', owner_id: 'room_1', owner_name: 'General' },
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

function seed(opts: { configs?: unknown[]; owners?: unknown[] } = {}): void {
  server.use(
    http.get('/api/projects/proj_1/graphrag-configs', () => HttpResponse.json(opts.configs ?? [])),
    http.get('/api/projects/proj_1/graphrag-configs/owner-options', () =>
      HttpResponse.json(opts.owners ?? OWNER_OPTIONS),
    ),
    http.get('/api/projects/proj_1/key-groups', () => HttpResponse.json(KEY_GROUPS)),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('GraphragConfigListView (Concept Maps overview)', () => {
  it('lists maps of all owner kinds with owner name + type', async () => {
    seed({ configs: [cfg(), cfg({ id: 'gr_2', owner_kind: 'workspace', owner_id: 'ws_1', owner_name: 'Main WS' })] })
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)
    // owner_name is data (renders verbatim); the kind label resolves to its i18n
    // key in the test harness (locale bundles aren't loaded).
    expect(wrapper.text()).toContain('Research Group')
    expect(wrapper.text()).toContain('Main WS')
    expect(wrapper.text()).toContain('agents.conceptMaps.ownerKinds.agent_group')
    expect(wrapper.text()).toContain('agents.conceptMaps.ownerKinds.workspace')
  })

  it('filters the list by owner type', async () => {
    seed({ configs: [cfg(), cfg({ id: 'gr_2', owner_kind: 'workspace', owner_id: 'ws_1', owner_name: 'Main WS' })] })
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)
    const filter = wrapper.find('#owner-kind-filter')
    await filter.setValue('workspace')
    await settle(wrapper)
    expect(wrapper.text()).toContain('Main WS')
    expect(wrapper.text()).not.toContain('Research Group')
  })

  it('enables create when a selectable owner and a key group exist', async () => {
    seed({ configs: [] })
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)
    expect(wrapper.find('button.s-btn--primary').attributes('disabled')).toBeUndefined()
  })

  it('disables create when no owner is available', async () => {
    seed({ configs: [cfg()], owners: [] })
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)
    expect(wrapper.find('button.s-btn--primary').attributes('disabled')).toBeDefined()
  })

  it('resets to page 1 when the owner-type filter changes, even if the filtered set still spans multiple pages', async () => {
    // Regression: the pagination watch used to only clamp `page` down when it
    // exceeded the new totalPages — a filter change that still leaves >= the
    // current page count left `page` untouched, showing an unrelated slice of
    // the newly filtered set instead of resetting to page 1.
    const agentGroupConfigs = Array.from({ length: 25 }, (_, i) =>
      cfg({ id: `ag_${i + 1}`, owner_kind: 'agent_group', owner_id: `grp_${i + 1}`, owner_name: `AG ${i + 1}` }),
    )
    const workspaceConfigs = Array.from({ length: 4 }, (_, i) =>
      cfg({ id: `ws_${i + 1}`, owner_kind: 'workspace', owner_id: `ws_${i + 1}`, owner_name: `WS ${i + 1}` }),
    )
    seed({ configs: [...agentGroupConfigs, ...workspaceConfigs] })
    const wrapper = await renderView(GraphragConfigListView, {
      routes,
      initialRoute: '/projects/proj_1/graphrag-configs',
    })
    await settle(wrapper)

    // 29 unfiltered items / PAGE_SIZE=10 -> 3 pages. Navigate to page 3.
    await wrapper.find('button[aria-label="app.pagination.next"]').trigger('click')
    await wrapper.find('button[aria-label="app.pagination.next"]').trigger('click')
    await settle(wrapper)
    expect(wrapper.text()).toContain('AG 21')

    // Filter to "agent_group" — still 25 items = still 3 pages, so the old
    // clamp-only logic left `page` at 3. The fix must reset to page 1.
    await wrapper.find('#owner-kind-filter').setValue('agent_group')
    await settle(wrapper)
    expect(wrapper.text()).toContain('AG 1')
    expect(wrapper.text()).not.toContain('AG 21')
  })
})
