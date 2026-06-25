<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { XCircleIcon } from '@heroicons/vue/24/outline'
import { SButton, SLoadingSpinner } from '@shared/ui'
import { ApiError } from '@shared/errors'
import { enrollGuest } from '../api'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

// `invalid` = token rejected (permanent, no retry); `error` = transient
// network/server failure (retryable).
const state = ref<'enrolling' | 'invalid' | 'error'>('enrolling')

async function doEnroll(): Promise<void> {
  state.value = 'enrolling'
  const chatroomId = route.params.chatroomId as string
  const token = route.params.guestToken as string
  try {
    await enrollGuest(chatroomId, token)
    // Strip the token from history (R24.43) before landing on the room.
    history.replaceState(null, '', `/c/${chatroomId}`)
    await router.replace({
      name: 'conversation.chatroom',
      params: { chatroomId },
    })
  } catch (e) {
    if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
      state.value = 'invalid'
    } else {
      state.value = 'error'
    }
  }
}

onMounted(doEnroll)
</script>

<template>
  <div class="auth-card guest-landing">
    <h1 class="auth-heading">
      {{ t('conversation.guest.title') }}
    </h1>

    <div
      class="guest-content"
      aria-live="polite"
    >
      <template v-if="state === 'enrolling'">
        <SLoadingSpinner
          size="md"
          :text="t('conversation.guest.enrolling')"
        />
      </template>

      <template v-else-if="state === 'invalid'">
        <XCircleIcon
          class="state-icon state-icon--failure"
          aria-hidden="true"
        />
        <p
          class="state-text"
          role="alert"
        >
          {{ t('conversation.guest.invalidToken') }}
        </p>
      </template>

      <template v-else>
        <XCircleIcon
          class="state-icon state-icon--failure"
          aria-hidden="true"
        />
        <p
          class="state-text"
          role="alert"
        >
          {{ t('conversation.guest.networkError') }}
        </p>
        <SButton
          variant="primary"
          class="state-action"
          @click="doEnroll"
        >
          {{ t('conversation.guest.retry') }}
        </SButton>
      </template>
    </div>
  </div>
</template>

<style scoped>
.auth-heading {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 24px;
  text-align: center;
}

.guest-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 16px;
  padding: 16px 0;
}

.state-icon {
  width: 48px;
  height: 48px;
}

.state-icon--failure {
  color: var(--color-danger);
}

.state-text {
  font-size: 0.875rem;
  color: var(--color-fg);
  margin: 0;
}

.state-action {
  margin-top: 8px;
}
</style>
