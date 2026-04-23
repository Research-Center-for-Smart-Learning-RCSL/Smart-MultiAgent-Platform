import type { RouteRecordRaw } from 'vue-router'

export const adminRoutes: RouteRecordRaw[] = [
  {
    path: '/admin',
    name: 'admin.home',
    component: () => import('./views/AdminHomeView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/users',
    name: 'admin.users',
    component: () => import('./views/AdminUsersView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/users/:userId',
    name: 'admin.userDetail',
    component: () => import('./views/AdminUserDetailView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/admins',
    name: 'admin.admins',
    component: () => import('./views/AdminAdminsView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/ip-bans',
    name: 'admin.ipBans',
    component: () => import('./views/AdminIpBansView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/orgs',
    name: 'admin.orgs',
    component: () => import('./views/AdminOrgsView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/projects',
    name: 'admin.projects',
    component: () => import('./views/AdminProjectsView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/audit',
    name: 'admin.audit',
    component: () => import('./views/AdminAuditView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/ops',
    name: 'admin.ops',
    component: () => import('./views/AdminOpsView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/rate-limits',
    name: 'admin.rateLimits',
    component: () => import('./views/AdminRateLimitsView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/metrics',
    name: 'admin.metrics',
    component: () => import('./views/AdminMetricsView.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
  {
    path: '/admin/impersonate',
    name: 'admin.impersonate',
    component: () => import('./views/AdminImpersonateLauncher.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
]
