<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { useInlineRename, useToast } from '@shared/composables'
import { orgsApi, type Org, type OrgQuotas } from '../api/orgs'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const org = ref<Org | null>(null)
const quotas = ref<OrgQuotas | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

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

// Generation guard: a later fetch (e.g. after a remount) must win even if an
// earlier slow request resolves after it.
let quotasGen = 0
async function loadQuotas(id: string): Promise<void> {
  const gen = ++quotasGen
  try {
    const { data } = await orgsApi.quotas(id)
    if (gen === quotasGen) quotas.value = data
  } catch {
    if (gen === quotasGen) quotas.value = null
  }
}

// Assign the PATCH response directly (it carries the new name + bumped version)
// rather than reloading — a reload failure must not mask a successful rename or
// leave a stale version that 412s the next rename.
const rename = useInlineRename({
  current: () => org.value?.name ?? '',
  save: async (name) => {
    if (!org.value) return
    try {
      const { data } = await orgsApi.rename(org.value.id, name, org.value.version)
      org.value = data
    } catch (e) {
      toast.error(t('tenancy.orgs.renameError'))
      throw e
    }
  },
})

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
    toast.error(t('identity.errors.generic'))
  }
}

onMounted(load)
</script>

<template>
  <main>
    <p v-if="loading">
      {{ $t('tenancy.orgs.loading') }}
    </p>
    <p
      v-else-if="error"
      class="error"
    >
      {{ $t('tenancy.orgs.loadError') }}
    </p>
    <template v-else-if="org">
      <h1 v-if="!rename.renaming.value">
        {{ org.name }}
        <button @click="rename.start">
          {{ $t('tenancy.orgs.rename') }}
        </button>
      </h1>
      <form
        v-else
        @submit.prevent="rename.save"
      >
        <label>
          {{ $t('tenancy.orgs.renameLabel') }}
          <input
            v-model="rename.nameDraft.value"
            required
          >
        </label>
        <button type="submit">
          {{ $t('app.save') }}
        </button>
        <button
          type="button"
          @click="rename.cancel"
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

      <button @click="remove">
        {{ $t('tenancy.orgs.delete') }}
      </button>
    </template>
  </main>
</template>
