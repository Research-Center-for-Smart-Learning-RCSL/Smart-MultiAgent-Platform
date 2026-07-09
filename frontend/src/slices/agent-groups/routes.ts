import type { RouteRecordRaw } from 'vue-router'

export const agentGroupsRoutes: RouteRecordRaw[] = [
  {
    path: '/projects/:projectId/agent-groups',
    name: 'agentGroups.list',
    component: () => import('./views/AgentGroupListView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/agent-groups/:groupId',
    name: 'agentGroups.detail',
    component: () => import('./views/AgentGroupDetailView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
]
