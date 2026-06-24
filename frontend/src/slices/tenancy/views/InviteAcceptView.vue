<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SCard, SButton, SLoadingSpinner } from '@shared/ui'
import { isProblemWithType } from '@shared/transport'
import { CheckCircleIcon, XCircleIcon } from '@heroicons/vue/24/outline'
import { invitesApi } from '../api/invites'

const { t } = useI18n()

const state = ref<'accepting' | 'success' | 'failure'>('accepting')
const errorMessage = ref('')

function tokenFromHash(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get('token')
}

onMounted(async () => {
  const token = tokenFromHash()
  if (!token) {
    errorMessage.value = t('tenancy.invite.noToken')
    state.value = 'failure'
    return
  }
  try {
    await invitesApi.acceptByToken(token)
    state.value = 'success'
  } catch (e: unknown) {
    if (isProblemWithType(e, '/tenancy/invite-expired')) {
      errorMessage.value = t('tenancy.invite.expired')
    } else if (isProblemWithType(e, '/tenancy/invite-not-found')) {
      errorMessage.value = t('tenancy.invite.notFound')
    } else {
      errorMessage.value = t('tenancy.invite.acceptError')
    }
    state.value = 'failure'
  }
})
</script>

<template>
  <div class="accept-wrapper">
    <SCard
      variant="elevated"
      padding="lg"
      class="accept-card"
    >
      <h1 class="accept-title">
        {{ t('tenancy.invite.acceptTitle') }}
      </h1>

      <!-- Accepting state -->
      <div
        v-if="state === 'accepting'"
        class="accept-state"
      >
        <SLoadingSpinner :text="t('tenancy.invite.accepting')" />
      </div>

      <!-- Success state -->
      <div
        v-else-if="state === 'success'"
        class="accept-state"
      >
        <CheckCircleIcon class="result-icon success-icon" />
        <p class="result-text">
          {{ t('tenancy.invite.acceptSuccess') }}
        </p>
        <SButton
          variant="primary"
          as="router-link"
          :to="{ name: 'tenancy.inbox' }"
        >
          {{ t('tenancy.invite.goToInbox') }}
        </SButton>
      </div>

      <!-- Failure state -->
      <div
        v-else
        class="accept-state"
      >
        <XCircleIcon class="result-icon error-icon" />
        <p class="result-text">
          {{ t('tenancy.invite.acceptError') }}
        </p>
        <p
          v-if="errorMessage"
          class="error-detail"
        >
          {{ errorMessage }}
        </p>
        <SButton
          variant="primary"
          as="router-link"
          :to="{ name: 'tenancy.inbox' }"
        >
          {{ t('tenancy.invite.goToInbox') }}
        </SButton>
      </div>
    </SCard>
  </div>
</template>

<style scoped>
.accept-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 400px;
}

.accept-card {
  max-width: 400px;
  width: 100%;
  text-align: center;
}

.accept-title {
  font-size: 1.25rem;
  font-weight: 600;
  margin-bottom: 24px;
}

.accept-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.result-icon {
  width: 48px;
  height: 48px;
}

.success-icon {
  color: var(--color-success);
}

.error-icon {
  color: var(--color-danger);
}

.result-text {
  font-size: 1rem;
  font-weight: 500;
}

.error-detail {
  font-size: 0.875rem;
  color: var(--color-muted);
}

@media (max-width: 768px) {
  .accept-card {
    max-width: none;
    margin: 0 16px;
  }
}
</style>
