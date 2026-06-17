import type { RouteRecordRaw } from 'vue-router'

export const workflowRoutes: RouteRecordRaw[] = [
  {
    path: '/workspaces/:workspaceId/workflows',
    name: 'workflow.list',
    component: () => import('./views/WorkflowListView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/edit',
    name: 'workflow.editor',
    component: () => import('./views/WorkflowEditorView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/runs',
    name: 'workflow.runs',
    component: () => import('./views/WorkflowRunsListView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/workflow-runs/:runId',
    name: 'workflow.run',
    component: () => import('./views/WorkflowRunView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/workspaces/:workspaceId/workflows/:workflowId/backstage',
    name: 'workflow.backstage',
    component: () => import('./views/WorkflowBackstageView.vue'),
    // NOTE: project_owner access would require per-project role resolution in
    // the route guard, which is not available from global session state today.
    // For now only admins can reach the backstage; revisit when per-project
    // role context is wired into the router.
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/agents/:agentId/orchestration',
    name: 'workflow.agentOrchestration',
    component: () => import('./views/AgentOrchestrationView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
]
