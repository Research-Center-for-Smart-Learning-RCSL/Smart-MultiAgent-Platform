<script setup lang="ts">
import { RouterLink } from 'vue-router'
import BrandLogo from '@app/components/BrandLogo.vue'
</script>

<template>
  <div class="auth-layout">
    <div class="auth-layout__wrapper">
      <div class="auth-layout__logo">
        <RouterLink
          to="/"
          class="auth-layout__logo-link"
        >
          <BrandLogo size="lg" />
        </RouterLink>
      </div>
      <slot />
    </div>
  </div>
</template>

<style scoped>
.auth-layout {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100dvh;
  background: var(--color-surface);
  /* max(), not addition: on a device with no cutout every env() is 0px and the
     designed 16px gutter is what survives; on one with a cutout the larger of
     the two wins. A bare env() would shrink the gutter below its design value.
     Unauthenticated routes render outside AppShell, so they carry their own. */
  padding: max(var(--space-4), env(safe-area-inset-top, 0px))
    max(var(--space-4), env(safe-area-inset-right, 0px))
    max(var(--space-4), env(safe-area-inset-bottom, 0px))
    max(var(--space-4), env(safe-area-inset-left, 0px));
}

.auth-layout__wrapper {
  width: 100%;
  max-width: 420px;
}

.auth-layout__logo-link {
  display: inline-flex;
  border-radius: 4px;
  outline-offset: 4px;
}

.auth-layout__logo {
  display: flex;
  justify-content: center;
  margin-bottom: var(--space-6);
  /* Gentle entrance so the auth pages feel continuous with the landing intro;
     frozen for reduced-motion users below. */
  animation: auth-logo-in 0.5s ease-out both;
}

@keyframes auth-logo-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .auth-layout__logo {
    animation: none;
  }
}

@media (max-width: 479px) {
  .auth-layout {
    padding: 0;
    align-items: flex-start;
  }

  .auth-layout__wrapper {
    max-width: none;
    min-height: 100dvh;
    /* The root drops its padding at xs, so the insets move here with the
       gutters they have to survive. */
    padding: max(var(--space-6), env(safe-area-inset-top, 0px))
      max(var(--space-4), env(safe-area-inset-right, 0px))
      max(var(--space-6), env(safe-area-inset-bottom, 0px))
      max(var(--space-4), env(safe-area-inset-left, 0px));
  }
}
</style>
