import { describe, it, expect, beforeAll } from 'vitest'
import { i18n } from '@shared/i18n'
import { renderView } from '../../../../tests/utils'
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

    await wrapper.find('header button').trigger('click')

    const badges = wrapper.findAll('button span').filter((s) => s.text() === 'Unavailable')
    expect(badges).toHaveLength(UNAVAILABLE_NODE_TYPES.length)
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
