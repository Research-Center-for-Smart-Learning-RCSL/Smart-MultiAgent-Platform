import type { RouteRecordRaw } from 'vue-router'

export const promptStudioRoutes: RouteRecordRaw[] = [
  {
    path: '/account/prompt-assistant',
    name: 'prompt-studio.personal',
    component: () => import('./views/PersonalPromptStudioView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/orgs/:orgId/prompt-assistant',
    name: 'prompt-studio.org',
    component: () => import('./views/OrgPromptStudioView.vue'),
    meta: { requiresAuth: true },
  },
]
