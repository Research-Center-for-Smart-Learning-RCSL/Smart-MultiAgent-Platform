<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { orgsApi, type Org } from '../api/orgs'

const route = useRoute()
const router = useRouter()
const org = ref<Org | null>(null)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  const { data } = await orgsApi.get(route.params.id as string)
  org.value = data
}

async function remove(): Promise<void> {
  if (!org.value) return
  try {
    await orgsApi.remove(org.value.id)
    router.push({ name: 'tenancy.orgList' })
  } catch {
    error.value = 'generic'
  }
}

onMounted(load)
</script>

<template>
  <main v-if="org">
    <h1>{{ org.name }}</h1>
    <nav>
      <router-link :to="{ name: 'tenancy.orgMembers', params: { id: org.id } }">
        {{ $t('tenancy.orgs.members') }}
      </router-link>
      <router-link :to="{ name: 'tenancy.orgTransfer', params: { id: org.id } }">
        {{ $t('tenancy.orgs.transferOwnership') }}
      </router-link>
    </nav>
    <p>v{{ org.version }} — {{ org.created_at }}</p>
    <p v-if="error" class="error">{{ error }}</p>
    <button @click="remove">{{ $t('tenancy.orgs.delete') }}</button>
  </main>
</template>
