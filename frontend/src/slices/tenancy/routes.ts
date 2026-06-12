import type { RouteRecordRaw } from 'vue-router'

export const tenancyRoutes: RouteRecordRaw[] = [
  {
    path: '/orgs',
    name: 'tenancy.orgList',
    component: () => import('./views/OrgListView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/orgs/:id',
    name: 'tenancy.orgDetail',
    component: () => import('./views/OrgDetailView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/orgs/:id/members',
    name: 'tenancy.orgMembers',
    component: () => import('./views/OrgMembersView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/orgs/:id/transfer',
    name: 'tenancy.orgTransfer',
    component: () => import('./views/OrgTransferView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/projects',
    name: 'tenancy.projectList',
    component: () => import('./views/ProjectListView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
  {
    path: '/projects/:id',
    name: 'tenancy.projectDetail',
    component: () => import('./views/ProjectDetailView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/projects/:id/members',
    name: 'tenancy.projectMembers',
    component: () => import('./views/ProjectMembersView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/invites',
    name: 'tenancy.inbox',
    component: () => import('./views/InboxInvitesView.vue'),
    meta: { requiresAuth: true },
  },
  {
    // Target of the emailed invite link (R6.09). The plaintext token rides in
    // the URL fragment; this view POSTs it to /invites/accept-by-token. Guarded
    // so an unregistered invitee is sent through sign-up first (R6.11).
    path: '/invites/accept',
    name: 'tenancy.inviteAccept',
    component: () => import('./views/InviteAcceptView.vue'),
    meta: { requiresAuth: true, requiresVerifiedEmail: true },
  },
]
