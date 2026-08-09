<template>
  <SCard>
    <h2 class="text-sm font-semibold text-[var(--color-fg)]">
      {{ $t('admin.activities.policy.heading') }}
    </h2>
    <p class="mt-1 text-xs text-[var(--color-muted)]">
      {{ $t('admin.activities.policy.help') }}
    </p>

    <SQueryError
      v-if="query.isError.value"
      class="mt-3"
      :message="$t('admin.common.loadError')"
      :retry-label="$t('admin.common.retry')"
      @retry="query.refetch()"
    />

    <p
      v-else-if="query.isPending.value"
      class="mt-3 text-xs text-[var(--color-muted)]"
    >
      {{ $t('admin.common.loading') }}
    </p>

    <form
      v-else
      class="mt-4 flex flex-col gap-4"
      @submit.prevent="onSubmit"
    >
      <p
        v-if="!isSaved"
        class="text-xs text-[var(--color-muted)]"
        data-testid="policy-unsaved"
      >
        {{ $t('admin.activities.policy.neverSaved') }}
      </p>

      <SFormField
        :label="$t('admin.activities.agentVisibility')"
        name="expose_payload_to_agent"
        :help="$t('admin.activities.policy.exposeHelp')"
      >
        <div class="flex flex-col gap-2">
          <SCheckbox
            v-model="form.expose_payload_to_agent_default"
            data-testid="policy-expose-default"
          >
            {{ $t('admin.activities.policy.defaultOn') }}
          </SCheckbox>
          <SCheckbox
            v-model="form.expose_payload_to_agent_locked"
            data-testid="policy-expose-locked"
          >
            {{ $t('admin.activities.policy.lock') }}
          </SCheckbox>
        </div>
      </SFormField>

      <SFormField
        :label="$t('admin.activities.roomVisibility')"
        name="echo_includes_content"
        :help="$t('admin.activities.policy.echoHelp')"
      >
        <div class="flex flex-col gap-2">
          <SCheckbox
            v-model="form.echo_includes_content_default"
            data-testid="policy-echo-default"
          >
            {{ $t('admin.activities.policy.defaultOn') }}
          </SCheckbox>
          <SCheckbox
            v-model="form.echo_includes_content_locked"
            data-testid="policy-echo-locked"
          >
            {{ $t('admin.activities.policy.lock') }}
          </SCheckbox>
        </div>
      </SFormField>

      <SFormField
        :label="$t('admin.activities.retention')"
        name="retention_days"
        :help="$t('admin.activities.policy.retentionHelp')"
      >
        <div class="flex flex-wrap items-center gap-3">
          <div>
            <label
              for="policy-retention-default"
              class="text-xs text-[var(--color-muted)]"
            >
              {{ $t('admin.activities.policy.retentionDefault') }}
            </label>
            <SInput
              id="policy-retention-default"
              v-model="retentionDefault"
              class="mt-1"
              data-testid="policy-retention-default"
            />
          </div>
          <div>
            <label
              for="policy-retention-max"
              class="text-xs text-[var(--color-muted)]"
            >
              {{ $t('admin.activities.policy.retentionMax') }}
            </label>
            <SInput
              id="policy-retention-max"
              v-model="retentionMax"
              class="mt-1"
              data-testid="policy-retention-max"
            />
          </div>
        </div>
      </SFormField>

      <p
        v-if="impact && impact.violating_types > 0"
        class="text-xs text-[var(--color-warning)]"
        role="status"
        data-testid="policy-impact"
      >
        {{
          $t(
            impact.approximate
              ? 'admin.activities.policy.impactApprox'
              : 'admin.activities.policy.impact',
            { count: impact.violating_types },
          )
        }}
      </p>

      <div class="flex items-center gap-3">
        <SButton
          variant="secondary"
          type="button"
          :loading="previewMutation.isPending.value"
          data-testid="policy-preview"
          @click="onPreview"
        >
          {{ $t('admin.activities.policy.preview') }}
        </SButton>
        <SButton
          variant="primary"
          type="submit"
          :loading="saveMutation.isPending.value"
          data-testid="policy-save"
        >
          {{ $t('admin.activities.policy.save') }}
        </SButton>
      </div>
    </form>
  </SCard>
</template>

<script setup lang="ts">
// Platform activity governance policy (R30.29). Read-then-replace with an
// If-Match version; the impact preview exists because tightening a policy is
// otherwise blind — its cost only shows up when a facilitator cannot start a
// class (R30.30).
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'

import { SButton, SCard, SCheckbox, SFormField, SInput, SQueryError } from '@shared/ui'
import { useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'

import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import type { ActivityPolicy, ActivityPolicyImpact, ActivityPolicyInput } from '../types'

const { t } = useI18n()
const toast = useToast()
const qc = useQueryClient()

const query = useQuery({
  queryKey: adminKeys.activityPolicy(),
  queryFn: () => adminApi.getActivityPolicy(),
})

const form = reactive<ActivityPolicyInput>({
  expose_payload_to_agent_default: true,
  expose_payload_to_agent_locked: false,
  echo_includes_content_default: false,
  echo_includes_content_locked: false,
  retention_days_default: null,
  retention_days_max: null,
})

const impact = ref<ActivityPolicyImpact | null>(null)

// version 0 is the server's "never saved" marker, which decides whether an
// If-Match is sent at all.
const isSaved = computed(() => (query.data.value?.version ?? 0) > 0)

watch(
  () => query.data.value,
  (policy: ActivityPolicy | undefined) => {
    if (!policy) return
    form.expose_payload_to_agent_default = policy.expose_payload_to_agent_default
    form.expose_payload_to_agent_locked = policy.expose_payload_to_agent_locked
    form.echo_includes_content_default = policy.echo_includes_content_default
    form.echo_includes_content_locked = policy.echo_includes_content_locked
    form.retention_days_default = policy.retention_days_default
    form.retention_days_max = policy.retention_days_max
  },
  { immediate: true },
)

// Text inputs, not type="number": SInput coerces an emptied numeric input to 0
// via Number(''), and 0 is not a legal retention bound — the field must be able
// to go back to "unset". Same reasoning as SchemaForm's number field.
function numberField(key: 'retention_days_default' | 'retention_days_max') {
  return computed<string>({
    get: () => (form[key] === null ? '' : String(form[key])),
    set: (raw) => {
      const trimmed = raw.trim()
      form[key] = trimmed === '' ? null : Number(trimmed)
    },
  })
}

const retentionDefault = numberField('retention_days_default')
const retentionMax = numberField('retention_days_max')

function snapshot(): ActivityPolicyInput {
  return { ...form }
}

const previewMutation = useMutation({
  mutationFn: () => adminApi.previewActivityPolicyImpact(snapshot()),
  onSuccess: (result) => {
    impact.value = result
    if (result.violating_types === 0) toast.success(t('admin.activities.policy.impactNone'))
  },
  onError: () => toast.error(t('admin.activities.policy.previewFailed')),
})

const saveMutation = useMutation({
  mutationFn: () =>
    adminApi.putActivityPolicy(snapshot(), isSaved.value ? (query.data.value?.version ?? null) : null),
  onSuccess: () => {
    impact.value = null
    void qc.invalidateQueries({ queryKey: adminKeys.activityPolicy() })
    toast.success(t('admin.activities.policy.saved'))
  },
  onError: (err) => {
    // 409 is the concurrent-edit guard, not a generic failure: someone else
    // changed the policy since this form loaded, so refetch rather than retry.
    if (err instanceof ApiError && err.status === 409) {
      void qc.invalidateQueries({ queryKey: adminKeys.activityPolicy() })
      toast.error(t('admin.activities.policy.conflict'))
      return
    }
    toast.error(t('admin.activities.policy.saveFailed'))
  },
})

function onPreview(): void {
  previewMutation.mutate()
}

function onSubmit(): void {
  saveMutation.mutate()
}
</script>
