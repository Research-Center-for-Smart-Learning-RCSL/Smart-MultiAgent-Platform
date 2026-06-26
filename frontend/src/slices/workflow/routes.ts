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
    meta: { requiresAuth: true, sidebarCollapsed: true, contentPadding: 'none' },
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
    // Per-project role isn't in the synchronous route guard (only the global
    // admin flag is). Authorization is resolved inside the view instead:
    // platform admins and project owners are allowed, everyone else is
    // redirected. The admin-only instruction-chain section is gated there too.
    meta: { requiresAuth: true },
  },
  {
    path: '/agents/:agentId/orchestration',
    name: 'workflow.agentOrchestration',
    component: () => import('./views/AgentOrchestrationView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
]
