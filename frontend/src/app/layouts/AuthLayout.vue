<script setup lang="ts">
</script>

<template>
  <div class="auth-layout">
    <div class="auth-layout__card">
      <div class="auth-layout__logo">
        SMAP
      </div>
      <div class="auth-layout__content">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-layout {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: var(--color-surface);
  padding: 16px;
}

.auth-layout__card {
  width: 100%;
  max-width: 420px;
  background: var(--color-bg);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  padding: 32px;
}

.auth-layout__logo {
  font-size: 24px;
  font-weight: 700;
  color: var(--color-accent);
  text-align: center;
  margin-bottom: 24px;
  user-select: none;
}

.auth-layout__content {
  width: 100%;
}

@media (max-width: 480px) {
  .auth-layout {
    padding: 0;
    align-items: flex-start;
  }

  .auth-layout__card {
    max-width: none;
    border-radius: 0;
    box-shadow: none;
    min-height: 100vh;
    padding: 32px 16px;
  }
}
</style>
