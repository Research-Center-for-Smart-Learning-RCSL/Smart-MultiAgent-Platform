<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { authApi, type Session } from '../api/auth'

const sessions = ref<Session[]>([])
const loading = ref(true)

async function load(): Promise<void> {
  loading.value = true
  try {
    const { data } = await authApi.listSessions()
    sessions.value = data
  } finally {
    loading.value = false
  }
}

async function revoke(id: string): Promise<void> {
  await authApi.revokeSession(id)
  await load()
}

onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('identity.sessions.title') }}</h1>
    <p v-if="loading">…</p>
    <ul v-else>
      <li v-for="s in sessions" :key="s.id">
        <span>{{ s.user_agent || 'Unknown device' }} — {{ s.ip_inet }} — {{ s.last_used_at }}</span>
        <span v-if="s.is_current"> {{ $t('identity.sessions.currentBadge') }}</span>
        <button v-else @click="revoke(s.id)">{{ $t('identity.sessions.revoke') }}</button>
      </li>
    </ul>
  </main>
</template>
