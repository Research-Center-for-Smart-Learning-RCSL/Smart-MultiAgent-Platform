<script setup lang="ts">
import BrandLogo from '@app/components/BrandLogo.vue'
</script>

<template>
  <div class="auth-layout">
    <div class="auth-layout__wrapper">
      <div class="auth-layout__logo">
        <BrandLogo size="lg" />
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
  padding: 16px;
}

.auth-layout__wrapper {
  width: 100%;
  max-width: 420px;
}

.auth-layout__logo {
  display: flex;
  justify-content: center;
  margin-bottom: 24px;
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

@media (max-width: 480px) {
  .auth-layout {
    padding: 0;
    align-items: flex-start;
  }

  .auth-layout__wrapper {
    max-width: none;
    min-height: 100dvh;
    padding: 24px 16px;
  }
}
</style>
