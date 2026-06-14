import type { RouteRecordRaw } from 'vue-router'

export const notificationsRoutes: RouteRecordRaw[] = [
  {
    path: '/notifications',
    name: 'notifications.list',
    component: () => import('./views/NotificationsView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
]
