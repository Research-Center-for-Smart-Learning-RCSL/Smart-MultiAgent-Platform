<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { SButton } from '@shared/ui'
import { useSessionStore } from '@shared/stores/session'

const { t } = useI18n()
const router = useRouter()
const session = useSessionStore()

onMounted(() => {
  if (session.isAuthenticated) {
    router.replace({ path: '/orgs' })
  }
})
</script>

<template>
  <div class="landing">
    <h1 class="landing__title">
      {{ t('app.landing.headline') }}
    </h1>
    <p class="landing__subtitle">
      {{ t('app.landing.subtitle') }}
    </p>
    <div class="landing__actions">
      <SButton
        variant="primary"
        size="lg"
        as="router-link"
        to="/register"
      >
        {{ t('app.landing.getStarted') }}
      </SButton>
      <SButton
        variant="secondary"
        size="lg"
        as="router-link"
        to="/login"
      >
        {{ t('app.landing.logIn') }}
      </SButton>
    </div>
  </div>
</template>

<style scoped>
.landing {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 12px;
  padding: 2rem 1rem 0;
}

.landing__title {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-fg);
  margin: 0;
}

.landing__subtitle {
  font-size: 1rem;
  color: var(--color-muted);
  max-width: 360px;
  line-height: 1.5;
  margin: 0;
}

.landing__actions {
  display: flex;
  gap: 12px;
  margin-top: 12px;
}
</style>
