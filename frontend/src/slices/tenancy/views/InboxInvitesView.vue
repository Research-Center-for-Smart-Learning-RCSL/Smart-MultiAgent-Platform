<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { invitesApi, type Invite } from '../api/invites'
import { useSessionStore } from '@slices/identity'
import { isProblemWithType } from '@shared/transport'

const invites = ref<Invite[]>([])
const error = ref<string | null>(null)
const session = useSessionStore()

async function load(): Promise<void> {
  const { data } = await invitesApi.list('pending')
  invites.value = data
}

async function accept(id: string): Promise<void> {
  error.value = null
  try {
    await invitesApi.accept(id)
    await load()
  } catch (e: unknown) {
    // R6.11 — unverified users cannot accept Guest-role invites.
    if (isProblemWithType(e, '/auth/email-unverified')) error.value = 'unverified'
    else error.value = 'generic'
  }
}

async function reject(id: string): Promise<void> {
  await invitesApi.reject(id)
  await load()
}

onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('tenancy.invites.inboxTitle') }}</h1>
    <p v-if="!session.isVerified" class="warning">
      {{ $t('tenancy.invites.unverifiedBlock') }}
    </p>
    <p v-if="error === 'unverified'" class="error">
      {{ $t('tenancy.invites.unverifiedBlock') }}
    </p>
    <p v-if="invites.length === 0">{{ $t('tenancy.invites.empty') }}</p>
    <ul v-else>
      <li v-for="i in invites" :key="i.id">
        [{{ i.scope_type }}] {{ i.scope_name }} — {{ i.role }}
        <button @click="accept(i.id)">{{ $t('tenancy.invites.accept') }}</button>
        <button @click="reject(i.id)">{{ $t('tenancy.invites.reject') }}</button>
      </li>
    </ul>
  </main>
</template>
