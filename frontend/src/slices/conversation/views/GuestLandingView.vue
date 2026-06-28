<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { XCircleIcon } from '@heroicons/vue/24/outline'
import { SButton, SFormField, SInput, SLoadingSpinner } from '@shared/ui'
import { ApiError } from '@shared/errors'
import { useSessionStore } from '@shared/stores/session'
import { enrollGuest } from '../api'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const session = useSessionStore()

// Route logged-out visitors through the landing intro (brand animation + logo)
// first; Landing forwards them on to login carrying this page as the return
// path, so the guest experience starts branded rather than on a bare form.
if (!session.isAuthenticated) {
  router.replace({
    name: 'root',
    query: { next: route.fullPath },
  })
}

// `idle` = waiting for user to fill in display name; `enrolling` = API in
// flight; `invalid` = token rejected (permanent, no retry); `error` =
// transient network/server failure (retryable — re-submits with same name).
const state = ref<'idle' | 'enrolling' | 'invalid' | 'error'>('idle')

const schema = toTypedSchema(
  z.object({
    displayName: z.string().trim().min(1).max(100),
  }),
)

const { handleSubmit, errors, defineField } = useForm({
  validationSchema: schema,
  initialValues: { displayName: '' },
})

const [displayName] = defineField('displayName')

const doEnroll = handleSubmit(async (values) => {
  state.value = 'enrolling'
  const chatroomId = route.params.chatroomId as string
  const token = route.params.guestToken as string
  try {
    await enrollGuest(chatroomId, token, values.displayName)
    // Strip the token from history (R24.43) before landing on the room.
    history.replaceState(null, '', `/c/${chatroomId}`)
    await router.replace({
      name: 'conversation.chatroom',
      params: { chatroomId },
    })
  } catch (e) {
    const permanent = e instanceof ApiError && [401, 403, 404].includes(e.status)
    state.value = permanent ? 'invalid' : 'error'
  }
})
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
      <template v-if="state === 'idle'">
        <p class="guest-desc">
          {{ t('conversation.guest.description') }}
        </p>
        <form
          class="guest-form"
          @submit.prevent="doEnroll"
        >
          <SFormField
            :label="t('conversation.guest.displayName')"
            name="displayName"
            :error="errors.displayName"
            required
          >
            <SInput
              v-model="displayName"
              :maxlength="100"
              :placeholder="t('conversation.guest.displayNamePlaceholder')"
              :error="!!errors.displayName"
            />
          </SFormField>
          <SButton
            type="submit"
            variant="primary"
            class="state-action"
            :disabled="!displayName.trim()"
          >
            {{ t('conversation.guest.enterChatroom') }}
          </SButton>
        </form>
      </template>

      <template v-else-if="state === 'enrolling'">
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

.guest-desc {
  font-size: 0.875rem;
  color: var(--color-muted);
  margin: 0;
}

.guest-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: 100%;
}

.state-action {
  margin-top: 8px;
}
</style>
