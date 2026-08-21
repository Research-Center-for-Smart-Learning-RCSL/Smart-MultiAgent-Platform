import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { i18n } from '@shared/i18n'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import { useWorkflowStore } from '../stores/workflow'

// jsdom has no SVG layout, so the real canvas throws during measurement. The
// stub also lets the F-31 tests observe fitView, which is the whole point:
// vue-flow provides the store from this view's own setup call down to the
// <VueFlow> child, so a spy here is the same instance the canvas would adopt.
const fitView = vi.fn()
vi.mock('@vue-flow/core', () => ({
  VueFlow: { name: 'VueFlow', template: '<div class="vue-flow"><slot /></div>' },
  useVueFlow: () => ({ fitView }),
}))
vi.mock('@vue-flow/background', () => ({ Background: { template: '<div />' } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { template: '<div />' } }))

import WorkflowEditorView from '../views/WorkflowEditorView.vue'
import { NODE_PALETTE_GROUPS, UNAVAILABLE_NODE_TYPES } from '../constants'
import en from '../locales/en.json'

const routes = [
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/edit',
    name: 'workflow.editor',
    component: WorkflowEditorView,
  },
  {
    path: '/workspaces/:workspaceId/workflows',
    name: 'workflow.list',
    component: { template: '<div />' },
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/runs',
    name: 'workflow.runs',
    component: { template: '<div />' },
  },
]

beforeAll(() => {
  i18n.global.mergeLocaleMessage('en', en as Record<string, unknown>)
})

describe('WorkflowEditorView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(WorkflowEditorView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/edit',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('contains the toolbar header', async () => {
    const wrapper = await renderView(WorkflowEditorView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/edit',
    })
    // The editor toolbar is inside a <header> with the back-link and action buttons.
    expect(wrapper.find('.workflow-editor header').exists()).toBe(true)
  })

  // AC-7 — the palette offered subagent_spawn beside working node types with no
  // qualification, so an author had no way to know the node cannot execute.
  it('badges unavailable node types in the palette', async () => {
    const wrapper = await renderView(WorkflowEditorView, {
      routes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/edit',
    })

    await wrapper.find('.workflow-editor header button').trigger('click')

    const badges = wrapper.findAll('button span').filter((s) => s.text() === 'Unavailable')
    expect(badges).toHaveLength(UNAVAILABLE_NODE_TYPES.length)
  })

  // F-31: the load-error, conflict and lint bars are flex-column siblings of
  // the canvas, so each one appearing shrinks the canvas and clips whatever
  // fit-view-on-init had fitted. Nothing re-applied the viewport.
  describe('re-fits the canvas when a bar changes the available area', () => {
    beforeEach(() => {
      fitView.mockClear()
    })

    // The editor only starts loading once useBackstageGuard concludes the
    // visitor is authorized, and the admin flag short-circuits the whole
    // workspace -> project -> membership resolution.
    function signInAsAdmin(): void {
      const session = useSessionStore()
      session.me = {
        id: 'u_1', email: 'a@smap.test', email_verified: true, is_admin: true, status: 'active',
      }
    }

    async function mountLoaded() {
      server.use(
        http.get('/api/workspaces/ws_1/workflows', () =>
          HttpResponse.json([
            {
              id: 'wf_1',
              name: 'Test Workflow',
              project_id: 'proj_1',
              workspace_id: 'ws_1',
              version: 1,
              definition: { nodes: [], edges: [] },
            },
          ]),
        ),
      )
      const wrapper = await renderView(WorkflowEditorView, {
        routes,
        initialRoute: '/workspaces/ws_1/workflows/wf_1/edit',
      })
      signInAsAdmin()
      await new Promise((r) => setTimeout(r, 60))
      await wrapper.vm.$nextTick()
      fitView.mockClear()
      return wrapper
    }

    it('re-fits when the lint status bar appears', async () => {
      const wrapper = await mountLoaded()

      useWorkflowStore().lintRan = true
      await wrapper.vm.$nextTick()
      await wrapper.vm.$nextTick()

      expect(fitView).toHaveBeenCalled()
    })

    it('re-fits when the load-error bar appears', async () => {
      // The default handler lists no workflows for the workspace, so the load
      // resolves to the not-found bar rather than to a canvas.
      const wrapper = await renderView(WorkflowEditorView, {
        routes,
        initialRoute: '/workspaces/ws_1/workflows/wf_1/edit',
      })
      signInAsAdmin()
      await new Promise((r) => setTimeout(r, 60))
      await wrapper.vm.$nextTick()
      await wrapper.vm.$nextTick()

      expect(wrapper.find('[role="alert"]').exists()).toBe(true)
      expect(fitView).toHaveBeenCalled()
    })

    it('does not re-fit on an unrelated store change', async () => {
      const wrapper = await mountLoaded()

      useWorkflowStore().dirty = true
      await wrapper.vm.$nextTick()
      await wrapper.vm.$nextTick()

      expect(fitView).not.toHaveBeenCalled()
    })
  })

  it('keeps every unavailable type in the palette rather than removing it', () => {
    // Q-4/R5: workflows.definition is a validated JSONB blob, so dropping a type
    // would make saved workflows containing it unloadable. Badge, do not remove.
    const offered = NODE_PALETTE_GROUPS.flatMap((g) => g.types)
    for (const nt of UNAVAILABLE_NODE_TYPES) {
      expect(offered).toContain(nt)
    }
  })
})
