<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfirmDialog } from '@shared/composables'
import { orgsApi, type Org } from '../api/orgs'

const { t } = useI18n()
const { confirm } = useConfirmDialog()

const orgs = ref<Org[]>([])
const name = ref('')
const loading = ref(true)
const loadError = ref<string | null>(null)
const createError = ref<string | null>(null)
const creating = ref(false)

async function load(): Promise<void> {
  loadError.value = null
  loading.value = true
  try {
    const { data } = await orgsApi.list()
    orgs.value = data
  } catch {
    loadError.value = 'tenancy.orgs.loadError'
  } finally {
    loading.value = false
  }
}

async function create(): Promise<void> {
  const trimmed = name.value.trim()
  if (!trimmed || creating.value) return

  const ok = await confirm({ title: t('tenancy.orgs.createConfirmTitle'), message: t('tenancy.orgs.createConfirm', { name: trimmed }), variant: 'info', confirmLabel: t('tenancy.orgs.create'), cancelLabel: t('tenancy.orgs.cancel') })
  if (!ok) return

  createError.value = null
  creating.value = true
  try {
    await orgsApi.create(trimmed)
    name.value = ''
    await load()
  } catch {
    createError.value = 'tenancy.orgs.createError'
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('tenancy.orgs.listTitle') }}</h1>
    <form @submit.prevent="create">
      <label for="org-name-input">
        {{ $t('tenancy.orgs.createLabel') }}
      </label>
      <input
        id="org-name-input"
        v-model="name"
        :placeholder="$t('tenancy.orgs.namePlaceholder')"
        :aria-describedby="createError ? 'org-create-error' : 'org-name-help'"
        :aria-invalid="createError ? 'true' : 'false'"
      >
      <small id="org-name-help">
        {{ $t('tenancy.orgs.nameHelp') }}
      </small>
      <button
        type="submit"
        :disabled="creating || !name.trim()"
      >
        {{ creating ? $t('tenancy.orgs.creating') : $t('tenancy.orgs.create') }}
      </button>
    </form>
    <p
      v-if="createError"
      id="org-create-error"
      role="alert"
      class="error"
    >
      {{ $t(createError) }}
    </p>
    <p
      v-if="loadError"
      role="alert"
      class="error"
    >
      {{ $t(loadError) }}
      <button
        type="button"
        @click="load"
      >
        {{ $t('tenancy.orgs.retry') }}
      </button>
    </p>
    <p v-if="loading">
      …
    </p>
    <ul v-else-if="orgs.length">
      <li
        v-for="o in orgs"
        :key="o.id"
      >
        <router-link :to="{ name: 'tenancy.orgDetail', params: { id: o.id } }">
          {{ o.name }}
        </router-link>
      </li>
    </ul>
    <p v-else-if="!loadError">
      {{ $t('tenancy.orgs.empty') }}
    </p>
  </main>
</template>

<style scoped>
.error {
  color: var(--color-danger);
}
</style>
