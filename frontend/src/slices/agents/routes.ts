import type { RouteRecordRaw } from 'vue-router'

export const agentsRoutes: RouteRecordRaw[] = [
  {
    path: '/projects/:projectId/agents',
    name: 'agents.list',
    component: () => import('./views/AgentListView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/agents/:agentId',
    name: 'agents.detail',
    component: () => import('./views/AgentDetailView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
]
