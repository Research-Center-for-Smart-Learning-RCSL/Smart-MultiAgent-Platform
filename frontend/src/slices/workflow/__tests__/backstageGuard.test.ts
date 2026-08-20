// The workflow surface became Admin-or-project-owner ([R14.10], dossier
// 2026-08-20-orchestration-room-scoped-reads AC-6). These pin the client half of
// that: a plain member must be sent away rather than left on a page whose every
// request answers 403, and no owner-only request may be fired on the way out.

import { describe, it, expect, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import WorkflowListView from '../views/WorkflowListView.vue'
import WorkflowRunsListView from '../views/WorkflowRunsListView.vue'
import WorkflowRunView from '../views/WorkflowRunView.vue'

const listRoutes = [
  { path: '/workspaces/:workspaceId/workflows', name: 'workflow.list', component: WorkflowListView },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/edit',
    name: 'workflow.editor',
    component: { template: '<div />' },
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/runs',
    name: 'workflow.runs',
    component: WorkflowRunsListView,
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/backstage',
    name: 'workflow.backstage',
    component: { template: '<div />' },
  },
]

const runRoutes = [{ path: '/workflow-runs/:runId', name: 'workflow.run', component: WorkflowRunView }]

function signIn(role: 'owner' | 'member'): void {
  const session = useSessionStore()
  session.me = { id: 'u_1', email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
  server.use(
    http.get('/api/projects/proj_1/members', () =>
      HttpResponse.json([
        { user_id: 'u_1', email: 'u@smap.test', role, joined_at: '2026-01-01T00:00:00Z' },
      ]),
    ),
    // The owner verdict comes off the project, not the member list: an Org
    // Owner moderates a project while holding no membership row (R5.03), and
    // reading the list concluded the opposite of every server gate.
    http.get('/api/projects/proj_1', () =>
      HttpResponse.json({ id: 'proj_1', name: 'Test Project', is_moderator: role === 'owner' }),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 160))
  await wrapper.vm.$nextTick()
}

describe('workflow views are backstage-gated', () => {
  let listWorkflowsHits = 0
  let listRunsHits = 0

  beforeEach(() => {
    listWorkflowsHits = 0
    listRunsHits = 0
    server.use(
      http.get('/api/workspaces/:workspaceId/workflows', () => {
        listWorkflowsHits += 1
        return HttpResponse.json([])
      }),
      http.get('/api/workflows/:workflowId/runs', () => {
        listRunsHits += 1
        return HttpResponse.json([])
      }),
    )
  })

  it('redirects a plain project member away from the workflow list', async () => {
    const wrapper = await renderView(WorkflowListView, {
      routes: listRoutes,
      initialRoute: '/workspaces/ws_1/workflows',
    })
    signIn('member')
    await settle(wrapper)

    expect(wrapper.vm.$route.name).toBe('root')
    // The redirect is navigation, not cancellation: the query must have been
    // disabled too, or the member still sends a request that just 403s.
    expect(listWorkflowsHits).toBe(0)
  })

  it('leaves the workflow list in place for a project owner', async () => {
    const wrapper = await renderView(WorkflowListView, {
      routes: listRoutes,
      initialRoute: '/workspaces/ws_1/workflows',
    })
    signIn('owner')
    await settle(wrapper)

    expect(wrapper.vm.$route.name).toBe('workflow.list')
    expect(listWorkflowsHits).toBeGreaterThan(0)
  })

  it('keeps an Org Owner who holds no project membership row in place', async () => {
    // The regression a post-close /code-review found. Ownership is inherited
    // (R5.03): this person moderates every project of the org, so every server
    // gate admits them — but they appear in no `project_members` row, so the
    // client's old member-list reading concluded "not an owner" and redirected
    // them off a page the server was serving.
    const session = useSessionStore()
    session.me = { id: 'u_1', email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
    server.use(
      http.get('/api/projects/proj_1/members', () => HttpResponse.json([])),
      http.get('/api/projects/proj_1', () =>
        HttpResponse.json({ id: 'proj_1', name: 'Test Project', is_moderator: true }),
      ),
    )
    const wrapper = await renderView(WorkflowListView, {
      routes: listRoutes,
      initialRoute: '/workspaces/ws_1/workflows',
    })
    await settle(wrapper)

    expect(wrapper.vm.$route.name).toBe('workflow.list')
    expect(listWorkflowsHits).toBeGreaterThan(0)
  })

  it('redirects a plain project member away from the run list', async () => {
    const wrapper = await renderView(WorkflowRunsListView, {
      routes: listRoutes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/runs',
    })
    signIn('member')
    await settle(wrapper)

    expect(wrapper.vm.$route.name).toBe('root')
    expect(listRunsHits).toBe(0)
  })

  it('keeps the run list and the backstage link for a project owner', async () => {
    const wrapper = await renderView(WorkflowRunsListView, {
      routes: listRoutes,
      initialRoute: '/workspaces/ws_1/workflows/wf_1/runs',
    })
    signIn('owner')
    await settle(wrapper)

    expect(wrapper.vm.$route.name).toBe('workflow.runs')
    expect(listRunsHits).toBeGreaterThan(0)
  })
})

describe('WorkflowRunView on a 403', () => {
  it('shows an empty state instead of a blank page or a toast', async () => {
    // This route is keyed by run id alone, so there is no project to resolve a
    // role against up front — the server's 403 is the only signal.
    // Shaped like the real refusal: `_http_exception_handler` turns every
    // HTTPException into problem+json, and only a body carrying `type` becomes
    // a typed `PermissionError` on the client (`transport/problem-json.ts`).
    const forbidden = () =>
      HttpResponse.json(
        {
          type: 'https://smap.local/problems/forbidden',
          title: 'Forbidden',
          status: 403,
          detail: 'workflow reads require Admin or a project owner',
        },
        { status: 403, headers: { 'Content-Type': 'application/problem+json' } },
      )
    server.use(
      http.get('/api/workflow-runs/:runId', forbidden),
      http.get('/api/workflow-runs/:runId/steps', forbidden),
    )
    const wrapper = await renderView(WorkflowRunView, {
      routes: runRoutes,
      initialRoute: '/workflow-runs/run_1',
    })
    await settle(wrapper)

    expect(wrapper.text()).toContain('workflow.run.forbiddenTitle')
    expect(wrapper.text()).not.toContain('workflow.run.steps')
  })

  it('renders the run normally when the caller may read it', async () => {
    const wrapper = await renderView(WorkflowRunView, {
      routes: runRoutes,
      initialRoute: '/workflow-runs/run_1',
    })
    await settle(wrapper)

    expect(wrapper.text()).not.toContain('workflow.run.forbiddenTitle')
    expect(wrapper.text()).toContain('workflow.run.steps')
  })
})
