import type { RouteRecordRaw } from 'vue-router'

export const adminRoutes: RouteRecordRaw[] = [
  {
    path: '/admin',
    // Shared admin shell: responsive section nav (sidebar / tabs / dropdown)
    // wrapping every section's content.
    component: () => import('./views/AdminLayout.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
    children: [
      {
        path: '',
        name: 'admin.home',
        component: () => import('./views/AdminHomeView.vue'),
      },
      {
        path: 'users',
        name: 'admin.users',
        component: () => import('./views/AdminUsersView.vue'),
      },
      {
        path: 'users/:userId',
        name: 'admin.userDetail',
        component: () => import('./views/AdminUserDetailView.vue'),
      },
      {
        path: 'admins',
        name: 'admin.admins',
        component: () => import('./views/AdminAdminsView.vue'),
      },
      {
        path: 'ip-bans',
        name: 'admin.ipBans',
        component: () => import('./views/AdminIpBansView.vue'),
      },
      {
        path: 'orgs',
        name: 'admin.orgs',
        component: () => import('./views/AdminOrgsView.vue'),
      },
      {
        path: 'projects',
        name: 'admin.projects',
        component: () => import('./views/AdminProjectsView.vue'),
      },
      {
        path: 'activities',
        name: 'admin.activities',
        component: () => import('./views/AdminActivitiesView.vue'),
      },
      {
        path: 'audit',
        name: 'admin.audit',
        component: () => import('./views/AdminAuditView.vue'),
      },
      {
        path: 'ops',
        name: 'admin.ops',
        component: () => import('./views/AdminOpsView.vue'),
      },
      {
        path: 'rate-limits',
        name: 'admin.rateLimits',
        component: () => import('./views/AdminRateLimitsView.vue'),
      },
      {
        path: 'metrics',
        name: 'admin.metrics',
        component: () => import('./views/AdminMetricsView.vue'),
      },
      {
        path: 'prompt-assistant',
        name: 'admin.promptStudio',
        component: () => import('@slices/prompt-studio').then((m) => m.AdminPromptStudioView),
      },
      {
        path: 'skills',
        name: 'admin.skills',
        component: () => import('@slices/skills').then((m) => m.AdminSkillsView),
      },
    ],
  },
  {
    // Impersonation launcher is a standalone full-page action, not a section.
    path: '/admin/impersonate',
    name: 'admin.impersonate',
    component: () => import('./views/AdminImpersonateLauncher.vue'),
    meta: { requiresAuth: true, requiredRoles: ['admin'] },
  },
]
