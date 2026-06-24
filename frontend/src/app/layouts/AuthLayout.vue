<script setup lang="ts">
</script>

<template>
  <div class="auth-layout">
    <div class="auth-layout__wrapper">
      <div class="auth-layout__logo">
        SMAP
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
  font-size: 24px;
  font-weight: 700;
  color: var(--color-accent);
  text-align: center;
  margin-bottom: 24px;
  user-select: none;
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
