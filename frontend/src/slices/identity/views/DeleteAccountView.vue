<script setup lang="ts">
import { ref, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  SPageHeader, SCard, SFormField, SInput,
  SCheckbox, SButton, SAlert,
} from '@shared/ui'
import { isProblemWithType } from '@shared/transport'
import { ApiError, RateLimitError } from '@shared/errors'
import { useConfirmDialog } from '@shared/composables'
import { authApi } from '../api/auth'
import { useSessionStore } from '../stores/session'

const { t } = useI18n()
const router = useRouter()
const session = useSessionStore()
const { confirm } = useConfirmDialog()

const password = ref('')
const confirmed = ref(false)
const serverError = ref<string | null>(null)
const blockedOrgIds = ref<string[]>([])
const submitting = ref(false)
const rateLimitSeconds = ref(0)
let rateLimitTimer: ReturnType<typeof setInterval> | undefined

const fieldErrors = ref<{ password?: string }>({})

function startRateLimitCountdown(seconds: number): void {
  rateLimitSeconds.value = seconds
  clearInterval(rateLimitTimer)
  rateLimitTimer = setInterval(() => {
    rateLimitSeconds.value--
    if (rateLimitSeconds.value <= 0) {
      clearInterval(rateLimitTimer)
      rateLimitTimer = undefined
      serverError.value = null
    }
  }, 1000)
}

onUnmounted(() => {
  clearInterval(rateLimitTimer)
})

async function submit(): Promise<void> {
  serverError.value = null
  blockedOrgIds.value = []

  if (!password.value) {
    fieldErrors.value.password = t('identity.validation.passwordRequired')
    return
  }
  fieldErrors.value.password = undefined

  const proceed = await confirm({
    title: t('identity.deleteAccount.title'),
    message: t('identity.deleteAccount.finalConfirm'),
    confirmLabel: t('identity.deleteAccount.finalConfirmButton'),
    variant: 'error',
  })
  if (!proceed) return

  submitting.value = true
  try {
    await authApi.deleteAccount(password.value)
    session.clear()
    router.push({ name: 'identity.login' })
  } catch (e: unknown) {
    if (e instanceof RateLimitError) {
      const seconds = Math.ceil(e.retryAfterMs / 1000)
      serverError.value = t('identity.errors.rateLimit')
      startRateLimitCountdown(seconds)
    } else if (e instanceof ApiError && e.status === 409) {
      const ids = e.extra.blocked_org_ids
      blockedOrgIds.value = Array.isArray(ids) ? (ids as string[]) : []
      serverError.value = t('identity.deleteAccount.blocked')
    } else if (isProblemWithType(e, '/auth/invalid-credentials')) {
      fieldErrors.value.password = t('identity.errors.invalidCredentials')
      password.value = ''
    } else {
      serverError.value = t('identity.errors.generic')
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div>
    <SPageHeader :title="$t('identity.deleteAccount.title')" />

    <SCard class="form-card">
      <SAlert
        variant="danger"
        class="warning-banner"
      >
        <template #default>
          {{ $t('identity.deleteAccount.warning') }}
        </template>
      </SAlert>

      <form @submit.prevent="submit">
        <SFormField
          :label="$t('identity.deleteAccount.password')"
          name="password"
          :error="fieldErrors.password"
          :help="$t('identity.deleteAccount.passwordHelp')"
          required
        >
          <SInput
            v-model="password"
            type="password"
            autocomplete="current-password"
            :disabled="submitting || rateLimitSeconds > 0"
            :error="!!fieldErrors.password"
          />
        </SFormField>

        <SFormField
          :label="$t('identity.deleteAccount.confirm')"
          name="confirmed"
          required
        >
          <SCheckbox
            v-model="confirmed"
            :disabled="submitting || rateLimitSeconds > 0"
          >
            {{ $t('identity.deleteAccount.confirm') }}
          </SCheckbox>
        </SFormField>

        <SAlert
          v-if="serverError"
          variant="danger"
          class="form-alert"
        >
          {{ serverError }}
          <ul
            v-if="blockedOrgIds.length"
            class="blocked-list"
          >
            <li
              v-for="id in blockedOrgIds"
              :key="id"
            >
              {{ id }}
            </li>
          </ul>
        </SAlert>

        <SButton
          type="submit"
          variant="danger"
          size="md"
          :loading="submitting"
          :disabled="submitting || !confirmed || !password || rateLimitSeconds > 0"
          :aria-disabled="!confirmed || !password ? true : undefined"
          :aria-busy="submitting"
          class="form-submit"
        >
          {{ submitting ? $t('identity.deleteAccount.deleting') : $t('identity.deleteAccount.submit') }}
        </SButton>
      </form>
    </SCard>
  </div>
</template>

<style scoped>
.form-card {
  max-width: 480px;
}

.warning-banner {
  margin-bottom: 20px;
}

form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-alert {
  margin: 0;
}

.form-submit {
  width: 100%;
}

.blocked-list {
  margin: 8px 0 0;
  padding-left: 20px;
  font-size: 0.75rem;
}

.blocked-list li {
  font-family: var(--font-mono, monospace);
}

@media (max-width: 768px) {
  .form-card {
    max-width: none;
  }
}
</style>
