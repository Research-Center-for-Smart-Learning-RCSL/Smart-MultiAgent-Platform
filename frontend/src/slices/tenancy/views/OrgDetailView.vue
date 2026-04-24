<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { orgsApi, type Org } from '../api/orgs'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const org = ref<Org | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  try {
    const { data } = await orgsApi.get(route.params.id as string)
    org.value = data
  } catch {
    error.value = 'generic'
  } finally {
    loading.value = false
  }
}

async function remove(): Promise<void> {
  if (!org.value) return
  try {
    await ElMessageBox.confirm(
      t('tenancy.orgs.deleteConfirm'),
      t('tenancy.orgs.deleteConfirmTitle'),
      { confirmButtonText: t('tenancy.orgs.delete'), cancelButtonText: t('app.cancel'), type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await orgsApi.remove(org.value.id)
    router.push({ name: 'tenancy.orgList' })
  } catch {
    ElMessage.error(t('identity.errors.generic'))
  }
}

onMounted(load)
</script>

<template>
  <main>
    <p v-if="loading">
      {{ $t('tenancy.orgs.loading') }}
    </p>
    <template v-else-if="org">
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
      <p
        v-if="error"
        class="error"
      >
        {{ error }}
      </p>
      <button @click="remove">
        {{ $t('tenancy.orgs.delete') }}
      </button>
    </template>
  </main>
</template>
