import type { RouteRecordRaw } from 'vue-router'

export const identityRoutes: RouteRecordRaw[] = [
  {
    path: '/register',
    name: 'identity.register',
    component: () => import('./views/RegisterView.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/login',
    name: 'identity.login',
    component: () => import('./views/LoginView.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/verify-email',
    name: 'identity.verifyEmail',
    component: () => import('./views/VerifyEmailView.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/password-reset',
    name: 'identity.passwordResetRequest',
    component: () => import('./views/PasswordResetRequestView.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/password-reset/confirm',
    name: 'identity.passwordResetConfirm',
    component: () => import('./views/PasswordResetConfirmView.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/account/password',
    name: 'identity.changePassword',
    component: () => import('./views/ChangePasswordView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/account/email',
    name: 'identity.changeEmail',
    component: () => import('./views/ChangeEmailView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/account/sessions',
    name: 'identity.sessions',
    component: () => import('./views/SessionsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/account/delete',
    name: 'identity.deleteAccount',
    component: () => import('./views/DeleteAccountView.vue'),
    meta: { requiresAuth: true },
  },
]
