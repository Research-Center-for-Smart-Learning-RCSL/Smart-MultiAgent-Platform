<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { orgsApi, type Org, type OrgQuotas } from '../api/orgs'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const org = ref<Org | null>(null)
const quotas = ref<OrgQuotas | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

const renaming = ref(false)
const nameDraft = ref('')

async function load(): Promise<void> {
  loading.value = true
  try {
    const { data } = await orgsApi.get(route.params.id as string)
    org.value = data
    loading.value = false
    // Advisory quotas are best-effort and must not gate the page render — fetch
    // them in the background and ignore failures.
    void loadQuotas(data.id)
  } catch {
    error.value = 'generic'
    loading.value = false
  }
}

async function loadQuotas(id: string): Promise<void> {
  try {
    quotas.value = (await orgsApi.quotas(id)).data
  } catch {
    quotas.value = null
  }
}

function startRename(): void {
  if (!org.value) return
  nameDraft.value = org.value.name
  renaming.value = true
}

async function saveRename(): Promise<void> {
  if (!org.value) return
  const name = nameDraft.value.trim()
  if (!name) return
  try {
    await orgsApi.rename(org.value.id, name, org.value.version)
    renaming.value = false
    await load() // refresh name + bumped version
  } catch {
    ElMessage.error(t('tenancy.orgs.renameError'))
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
      <h1 v-if="!renaming">
        {{ org.name }}
        <button @click="startRename">
          {{ $t('tenancy.orgs.rename') }}
        </button>
      </h1>
      <form
        v-else
        @submit.prevent="saveRename"
      >
        <label>
          {{ $t('tenancy.orgs.renameLabel') }}
          <input
            v-model="nameDraft"
            required
          >
        </label>
        <button type="submit">
          {{ $t('app.save') }}
        </button>
        <button
          type="button"
          @click="renaming = false"
        >
          {{ $t('app.cancel') }}
        </button>
      </form>
      <nav>
        <router-link :to="{ name: 'tenancy.orgMembers', params: { id: org.id } }">
          {{ $t('tenancy.orgs.members') }}
        </router-link>
        <router-link :to="{ name: 'tenancy.orgTransfer', params: { id: org.id } }">
          {{ $t('tenancy.orgs.transferOwnership') }}
        </router-link>
      </nav>
      <p>v{{ org.version }} — {{ org.created_at }}</p>

      <section
        v-if="quotas"
        class="quotas"
      >
        <h2>{{ $t('tenancy.orgs.quotas.title') }}</h2>
        <ul>
          <li>{{ $t('tenancy.orgs.quotas.users') }}: {{ quotas.users }} / {{ quotas.advisory_targets.users ?? '—' }}</li>
          <li>{{ $t('tenancy.orgs.quotas.projects') }}: {{ quotas.projects }} / {{ quotas.advisory_targets.projects ?? '—' }}</li>
          <li>{{ $t('tenancy.orgs.quotas.chatrooms') }}: {{ quotas.chatrooms }} / {{ quotas.advisory_targets.chatrooms ?? '—' }}</li>
          <li>{{ $t('tenancy.orgs.quotas.agents') }}: {{ quotas.agents }} / {{ quotas.advisory_targets.agents ?? '—' }}</li>
          <li>{{ $t('tenancy.orgs.quotas.workflows') }}: {{ quotas.workflows }} / {{ quotas.advisory_targets.workflows ?? '—' }}</li>
        </ul>
        <p class="muted">
          {{ $t('tenancy.orgs.quotas.advisoryNote') }}
        </p>
      </section>

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
