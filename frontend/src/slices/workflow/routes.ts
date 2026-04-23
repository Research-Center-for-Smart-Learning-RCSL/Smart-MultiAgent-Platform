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
    meta: { requiresAuth: true, requiresRole: ['admin', 'project_owner'] },
  },
]
