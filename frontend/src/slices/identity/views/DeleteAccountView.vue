<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import { authApi } from '../api/auth'
import { useSessionStore } from '../stores/session'

const { t } = useI18n()
const password = ref('')
const confirmed = ref(false)
const error = ref<string | null>(null)
// Org IDs returned by a 409 (R8.18): the caller is the Original Creator of
// these Orgs and must transfer the role or delete each Org before self-deleting.
const blockedOrgIds = ref<string[]>([])
const submitting = ref(false)
const session = useSessionStore()
const router = useRouter()

async function submit(): Promise<void> {
  error.value = null
  blockedOrgIds.value = []
  submitting.value = true
  try {
    await authApi.deleteAccount(password.value)
    // Server soft-deleted the account and killed every session; drop local
    // auth state and land on the login page.
    session.clear()
    router.push({ name: 'identity.login' })
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      const ids = e.extra.blocked_org_ids
      blockedOrgIds.value = Array.isArray(ids) ? (ids as string[]) : []
      error.value = t('identity.deleteAccount.blocked')
    } else if (e instanceof ApiError && e.status === 401) {
      error.value = t('identity.errors.invalid')
    } else {
      error.value = t('identity.errors.generic')
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="form-page">
    <h1>{{ $t('identity.deleteAccount.title') }}</h1>
    <p class="warning">
      {{ $t('identity.deleteAccount.warning') }}
    </p>
    <form @submit.prevent="submit">
      <label>
        {{ $t('identity.deleteAccount.password') }}
        <input
          v-model="password"
          type="password"
          required
        >
      </label>
      <label class="confirm">
        <input
          v-model="confirmed"
          type="checkbox"
          required
        >
        {{ $t('identity.deleteAccount.confirm') }}
      </label>
      <p
        v-if="error"
        class="error"
      >
        {{ error }}
      </p>
      <ul
        v-if="blockedOrgIds.length"
        class="blocked-orgs"
      >
        <li
          v-for="id in blockedOrgIds"
          :key="id"
        >
          {{ id }}
        </li>
      </ul>
      <button
        type="submit"
        class="danger"
        :disabled="submitting || !confirmed"
      >
        {{ $t('identity.deleteAccount.submit') }}
      </button>
    </form>
  </main>
</template>
