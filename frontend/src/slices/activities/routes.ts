import type { RouteRecordRaw } from 'vue-router'

// Owner-scoped activity-type management, a project-settings sibling route
// following the Skills precedent (slices/skills/routes.ts). The page itself
// re-checks project-owner authorization; the backend routes it calls are the
// authoritative gate.
export const activitiesRoutes: RouteRecordRaw[] = [
  {
    path: '/projects/:projectId/activity-types',
    name: 'activities.types',
    component: () => import('./views/ActivityTypesView.vue'),
    meta: { requiresAuth: true },
  },
]
