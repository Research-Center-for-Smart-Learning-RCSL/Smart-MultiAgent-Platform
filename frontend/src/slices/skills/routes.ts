import type { RouteRecordRaw } from 'vue-router'

// Project + org scopes route here; the platform view is mounted by the admin slice and
// agent scope lives inside the agent detail's binding panel (§6).
export const skillsRoutes: RouteRecordRaw[] = [
  {
    path: '/projects/:projectId/skills',
    name: 'skills.project',
    component: () => import('./views/ProjectSkillsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/orgs/:orgId/skills',
    name: 'skills.org',
    component: () => import('./views/OrgSkillsView.vue'),
    meta: { requiresAuth: true },
  },
]
