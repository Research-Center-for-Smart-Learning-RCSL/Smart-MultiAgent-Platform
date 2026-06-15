<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { invitesApi } from '../api/invites'

const state = ref<'accepting' | 'success' | 'failure'>('accepting')

// The invite token arrives in the URL fragment (`#token=…`) so it never reaches
// server logs or `Referer` headers — read it from the hash and POST it (SEC-8).
// The route is guarded (requiresAuth + requiresVerifiedEmail, R6.11), so an
// unregistered invitee is routed through login/register first and returns here.
function tokenFromHash(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get('token')
}

onMounted(async () => {
  const token = tokenFromHash()
  if (!token) {
    state.value = 'failure'
    return
  }
  try {
    await invitesApi.acceptByToken(token)
    state.value = 'success'
  } catch {
    state.value = 'failure'
  }
})
</script>

<template>
  <main class="form-page">
    <h1>{{ $t('tenancy.invites.acceptTitle') }}</h1>
    <p v-if="state === 'accepting'">
      {{ $t('tenancy.invites.accepting') }}
    </p>
    <p v-else-if="state === 'success'">
      {{ $t('tenancy.invites.acceptSuccess') }}
    </p>
    <p
      v-else
      class="error"
      role="alert"
    >
      {{ $t('tenancy.invites.acceptFailure') }}
    </p>
    <router-link :to="{ name: 'tenancy.inbox' }">
      {{ $t('tenancy.invites.goToInbox') }}
    </router-link>
  </main>
</template>
