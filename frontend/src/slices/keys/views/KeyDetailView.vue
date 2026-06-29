<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import {
  ArrowPathIcon,
  TrashIcon,
  ExclamationTriangleIcon,
} from '@heroicons/vue/24/outline'
import {
  SPageHeader,
  SCard,
  SButton,
  SStatusBadge,
  SBadge,
  SEmptyState,
  SLoadingSpinner,
  SAlert,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { useMyKeys } from '../composables/useMyKeys'
import { useKeyProjects } from '../composables/useKeyProjects'
import { keysApi, type KeyProject } from '../api/keys'
import { keysKeys } from '../queries'
import CapabilityChip from '../components/CapabilityChip.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirmDialog()
const keyId = computed(() => route.params.id as string)
const { keys, retest, remove } = useMyKeys()
const {
  projects,
  loading: projectsLoading,
  error: projectsError,
  withdraw,
} = useKeyProjects(keyId)

// Fetch the key by id rather than scanning the (page-limited) my-keys list,
// which would 404 a key past the first page for users with many keys.
const keyQuery = useQuery({
  queryKey: computed(() => keysKeys.key(keyId.value)),
  queryFn: async () => (await keysApi.get(keyId.value)).data,
})
const currentLoading = computed(() => keyQuery.isLoading.value)

const retesting = ref(false)
const deleting = ref(false)
const withdrawingProjectId = ref<string | null>(null)

const current = computed(
  () => keyQuery.data.value ?? keys.value.find((k) => k.id === keyId.value),
)

const breadcrumbs = computed(() => [
  { label: t('keys.list.title'), to: { name: 'keys.list' } },
  { label: current.value?.name ?? '' },
])

function formatDatetime(iso: string | null): string {
  if (!iso) return t('keys.detail.never')
  return new Date(iso).toLocaleString()
}

async function onRetest() {
  retesting.value = true
  try {
    await retest(keyId.value)
    const { data: key } = await keyQuery.refetch()
    if (key?.test_status === 'ok') {
      toast.success(t('keys.detail.retestValid'))
    } else if (key?.test_status === 'failed') {
      toast.warning(t('keys.detail.retestInvalid'))
    }
  } catch {
    toast.error(t('keys.detail.retestFailed'))
  } finally {
    retesting.value = false
  }
}

async function onWithdraw(project: KeyProject) {
  const message =
    project.agent_count > 0 || project.group_count > 0
      ? t('keys.detail.withdrawImpact', {
          name: current.value?.name ?? '',
          project: project.project_name,
          agents: project.agent_count,
          groups: project.group_count,
        })
      : t('keys.detail.withdrawImpactNone', {
          name: current.value?.name ?? '',
          project: project.project_name,
        })
  const ok = await confirm({
    title: t('keys.detail.withdrawTitle'),
    message,
    confirmLabel: t('keys.detail.withdrawConfirm'),
    variant: 'error',
  })
  if (!ok) return
  withdrawingProjectId.value = project.project_id
  try {
    await withdraw(project.project_id)
    toast.success(t('keys.detail.withdrawn'))
  } catch {
    toast.error(t('keys.detail.withdrawFailed'))
  } finally {
    withdrawingProjectId.value = null
  }
}

async function onDelete() {
  const ok = await confirm({
    title: t('keys.detail.deleteTitle'),
    message: t('keys.detail.deleteBody', { name: current.value?.name ?? '' }),
    confirmLabel: t('keys.detail.deleteConfirmLabel'),
    variant: 'error',
  })
  if (!ok) return
  deleting.value = true
  try {
    await remove(keyId.value)
    await router.replace({ name: 'keys.list' })
  } catch {
    toast.error(t('keys.detail.deleteFailed'))
  } finally {
    deleting.value = false
  }
}

</script>

<template>
  <main class="p-6">
    <!-- Not found state -->
    <div
      v-if="!currentLoading && !current"
      class="flex justify-center mt-12"
    >
      <SEmptyState
        :icon="ExclamationTriangleIcon"
        :title="$t('keys.detail.notFound')"
        :text="$t('keys.detail.notFoundDescription')"
      >
        <template #action>
          <SButton
            variant="secondary"
            :to="{ name: 'keys.list' }"
            as="router-link"
          >
            {{ $t('keys.detail.backToKeys') }}
          </SButton>
        </template>
      </SEmptyState>
    </div>

    <template v-if="current">
      <SPageHeader
        :title="current.name"
        :breadcrumbs="breadcrumbs"
      >
        <template #actions>
          <SButton
            variant="secondary"
            :loading="retesting"
            @click="onRetest"
          >
            <template #icon-left>
              <ArrowPathIcon class="w-4 h-4" />
            </template>
            {{ $t('keys.detail.retest') }}
          </SButton>
          <SButton
            variant="danger"
            :loading="deleting"
            @click="onDelete"
          >
            <template #icon-left>
              <TrashIcon class="w-4 h-4" />
            </template>
            {{ $t('keys.detail.delete') }}
          </SButton>
        </template>
      </SPageHeader>

      <SCard
        variant="elevated"
        padding="lg"
        class="mt-6"
      >
        <dl class="detail-dl">
          <div class="detail-row">
            <dt>{{ $t('keys.detail.provider') }}</dt>
            <dd><CapabilityChip :provider="current.provider" /></dd>
          </div>
          <div class="detail-row">
            <dt>{{ $t('keys.detail.name') }}</dt>
            <dd>{{ current.name }}</dd>
          </div>
          <div class="detail-row">
            <dt>{{ $t('keys.detail.preview') }}</dt>
            <dd>
              <code class="text-[13px] font-mono">{{ current.masked_preview }}</code>
            </dd>
          </div>
          <div class="detail-row">
            <dt>{{ $t('keys.detail.status') }}</dt>
            <dd>
              <SStatusBadge :status="current.test_status" />
              <span
                v-if="current.test_status === 'failed' && current.test_error"
                class="text-xs text-[var(--color-muted)] ml-2"
              >{{ current.test_error }}</span>
            </dd>
          </div>
          <div class="detail-row">
            <dt>{{ $t('keys.detail.lastTested') }}</dt>
            <dd>{{ formatDatetime(current.last_test_at) }}</dd>
          </div>
          <div class="detail-row border-b-0">
            <dt>{{ $t('keys.detail.created') }}</dt>
            <dd>{{ formatDatetime(current.created_at) }}</dd>
          </div>
        </dl>
      </SCard>

      <SCard
        variant="elevated"
        padding="lg"
        class="mt-6"
      >
        <h2 class="text-sm font-semibold text-[var(--color-fg)]">
          {{ $t('keys.detail.projectsTitle') }}
        </h2>
        <p class="text-xs text-[var(--color-muted)] mt-1 mb-4">
          {{ $t('keys.detail.projectsDescription') }}
        </p>

        <div
          v-if="projectsLoading"
          class="flex justify-center py-6"
        >
          <SLoadingSpinner />
        </div>
        <SAlert
          v-else-if="projectsError"
          variant="danger"
        >
          {{ $t('keys.detail.projectsError') }}
        </SAlert>
        <p
          v-else-if="projects.length === 0"
          class="text-sm text-[var(--color-muted)] py-2"
        >
          {{ $t('keys.detail.projectsEmpty') }}
        </p>
        <ul
          v-else
          class="flex flex-col"
        >
          <li
            v-for="p in projects"
            :key="p.project_id"
            class="project-row"
          >
            <div class="min-w-0">
              <div class="text-sm font-medium text-[var(--color-fg)] truncate">
                {{ p.project_name }}
              </div>
              <div class="mt-1">
                <SBadge
                  v-if="p.group_count > 0"
                  variant="info"
                  size="sm"
                >
                  {{ $t('keys.detail.bindingSummary', { groups: p.group_count, agents: p.agent_count }) }}
                </SBadge>
                <SBadge
                  v-else
                  variant="neutral"
                  size="sm"
                >
                  {{ $t('keys.detail.carriedOnly') }}
                </SBadge>
              </div>
            </div>
            <SButton
              variant="ghost"
              size="sm"
              :loading="withdrawingProjectId === p.project_id"
              @click="onWithdraw(p)"
            >
              {{ $t('keys.detail.withdraw') }}
            </SButton>
          </li>
        </ul>
      </SCard>
    </template>
  </main>
</template>

<style scoped>
.detail-dl {
  display: flex;
  flex-direction: column;
}

.detail-row {
  display: flex;
  align-items: center;
  min-height: 40px;
  border-bottom: 1px solid var(--color-border);
}

.project-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--color-border);
}

.project-row:last-child {
  border-bottom: 0;
}

.detail-row dt {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-muted);
  width: 140px;
  flex-shrink: 0;
}

.detail-row dd {
  font-size: 0.875rem;
  color: var(--color-fg);
}

@media (max-width: 767px) {
  .detail-row {
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    padding: 8px 0;
  }

  .detail-row dt {
    width: auto;
  }
}
</style>
