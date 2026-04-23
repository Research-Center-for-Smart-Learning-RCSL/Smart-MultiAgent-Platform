<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { orgsApi, type Org } from '../api/orgs'

const orgs = ref<Org[]>([])
const name = ref('')
const loading = ref(true)

async function load(): Promise<void> {
  const { data } = await orgsApi.list()
  orgs.value = data
  loading.value = false
}

async function create(): Promise<void> {
  if (!name.value.trim()) return
  await orgsApi.create(name.value.trim())
  name.value = ''
  await load()
}

onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('tenancy.orgs.listTitle') }}</h1>
    <form @submit.prevent="create">
      <label>{{ $t('tenancy.orgs.createLabel') }}</label>
      <input v-model="name" :placeholder="$t('tenancy.orgs.namePlaceholder')" />
      <button type="submit">{{ $t('tenancy.orgs.create') }}</button>
    </form>
    <p v-if="loading">…</p>
    <ul v-else>
      <li v-for="o in orgs" :key="o.id">
        <router-link :to="{ name: 'tenancy.orgDetail', params: { id: o.id } }">
          {{ o.name }}
        </router-link>
      </li>
    </ul>
  </main>
</template>
