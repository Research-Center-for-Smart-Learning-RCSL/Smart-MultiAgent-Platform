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
  {
    path: '/projects/:projectId/rag-configs',
    name: 'agents.ragConfigs',
    component: () => import('./views/RagConfigListView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/projects/:projectId/rag-configs/:configId',
    name: 'agents.ragConfig',
    component: () => import('./views/RagConfigDetailView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/projects/:projectId/graphrag-configs',
    name: 'agents.graphragConfigs',
    component: () => import('./views/GraphragConfigListView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/agents/:agentId/mcp',
    name: 'agents.mcp',
    component: () => import('./views/AgentMcpView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/projects/:projectId/mcp/egress-allowlist',
    name: 'agents.egressAllowlist',
    component: () => import('./views/McpEgressAllowlistView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
]
