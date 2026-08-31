<template>
  <SCard>
    <h2 class="text-sm font-semibold text-[var(--color-fg)]">
      {{ $t('admin.emailDomain.heading') }}
    </h2>
    <p class="mt-1 text-xs text-[var(--color-muted)]">
      {{ $t('admin.emailDomain.help') }}
    </p>

    <SQueryError
      v-if="query.isError.value"
      class="mt-3"
      :message="$t('admin.emailDomain.loadError')"
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
      <SAlert
        v-if="!editable"
        variant="warning"
        data-testid="email-domain-fenced"
      >
        {{ $t(fenceMessageKey) }}
      </SAlert>

      <SFormField
        :label="$t('admin.emailDomain.mode')"
        name="email-domain-mode"
        :help="$t('admin.emailDomain.modeHelp')"
      >
        <SSelect
          v-model="form.mode"
          :options="modeOptions"
          :disabled="!editable"
          data-testid="email-domain-mode"
        />
      </SFormField>

      <SFormField
        :label="$t('admin.emailDomain.allow')"
        name="email-domain-allow"
        :help="$t('admin.emailDomain.listHelp')"
      >
        <STextarea
          v-model="allowText"
          :rows="5"
          :disabled="!editable"
          :placeholder="$t('admin.emailDomain.listPlaceholder')"
          data-testid="email-domain-allow"
        />
      </SFormField>

      <SFormField
        :label="$t('admin.emailDomain.deny')"
        name="email-domain-deny"
        :help="$t('admin.emailDomain.listHelp')"
      >
        <STextarea
          v-model="denyText"
          :rows="5"
          :disabled="!editable"
          :placeholder="$t('admin.emailDomain.listPlaceholder')"
          data-testid="email-domain-deny"
        />
      </SFormField>

      <p
        v-if="rejectedEntries.length > 0"
        class="text-xs text-[var(--color-danger)]"
        role="alert"
        data-testid="email-domain-invalid"
      >
        {{ $t('admin.emailDomain.invalidEntries', { entries: rejectedEntries.join(', ') }) }}
      </p>

      <p
        class="text-xs text-[var(--color-muted)]"
        data-testid="email-domain-state"
      >
        {{
          $t('admin.emailDomain.stateLine', {
            state: $t(`admin.emailDomain.state.${policy?.rollout_state ?? 'active'}`),
            version: policy?.version ?? 0,
          })
        }}
      </p>

      <p
        v-if="rollbackReady"
        class="text-xs text-[var(--color-muted)]"
        data-testid="email-domain-rollback-marker"
      >
        {{ $t('admin.emailDomain.rollbackVerified', { version: policy?.legacy_mirrored_version ?? 0 }) }}
      </p>

      <div>
        <SButton
          variant="primary"
          type="submit"
          :loading="saveMutation.isPending.value"
          :disabled="!editable || rejectedEntries.length > 0"
          data-testid="email-domain-save"
        >
          {{ $t('admin.emailDomain.save') }}
        </SButton>
      </div>
    </form>
  </SCard>
</template>

<script setup lang="ts">
// Email-domain allow/deny policy (R19a.13). Read-then-replace guarded by the
// version the form loaded, and read-only outside the `active` rollout phase --
// the server says which phase it is in, so the form never has to discover a
// fence by attempting a write.
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'

import { SAlert, SButton, SCard, SFormField, SQueryError, SSelect, STextarea } from '@shared/ui'
import { useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'

import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import type { EmailDomainMode, EmailDomainPolicy } from '../types'

const { t } = useI18n()
const toast = useToast()
const qc = useQueryClient()

const query = useQuery({
  queryKey: adminKeys.emailDomainPolicy(),
  queryFn: () => adminApi.getEmailDomainPolicy(),
})

const policy = computed<EmailDomainPolicy | undefined>(() => query.data.value)
const editable = computed(() => policy.value?.editable === true)

const rollbackReady = computed(
  () =>
    policy.value != null &&
    policy.value.legacy_mirrored_version != null &&
    policy.value.legacy_mirrored_version === policy.value.version,
)

const fenceMessageKey = computed(() =>
  policy.value?.rollout_state === 'rollback_frozen'
    ? 'admin.emailDomain.fencedFrozen'
    : 'admin.emailDomain.fencedCompatibility',
)

const form = reactive<{ mode: EmailDomainMode }>({ mode: 'off' })
const allowText = ref('')
const denyText = ref('')

const modeOptions = computed(() => [
  { value: 'off', label: t('admin.emailDomain.modeOff') },
  { value: 'allow', label: t('admin.emailDomain.modeAllow') },
  { value: 'deny', label: t('admin.emailDomain.modeDeny') },
])

watch(
  policy,
  (loaded) => {
    if (!loaded) return
    form.mode = loaded.mode
    allowText.value = loaded.allow.join('\n')
    denyText.value = loaded.deny.join('\n')
  },
  { immediate: true },
)

/** Newline-separated, blank lines dropped. Order carries no meaning and the
 *  server de-duplicates, so this is a split rather than a parse. */
function toList(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

// Anything that is not a bare domain can never match a domain extracted from an
// address, so a policy holding one looks configured and admits everybody. The
// server rejects the same shapes; naming them here means the admin sees which
// line is wrong instead of a whole-request 422.
const BARE_DOMAIN = /^[^\s@/\\:?#*%]+\.[^\s@/\\:?#*%]+$/

const rejectedEntries = computed(() =>
  [...toList(allowText.value), ...toList(denyText.value)].filter(
    (entry) => !BARE_DOMAIN.test(entry) || entry.startsWith('.') || entry.endsWith('.') || entry.includes('..'),
  ),
)

const saveMutation = useMutation({
  mutationFn: () =>
    adminApi.putEmailDomainPolicy(
      { mode: form.mode, allow: toList(allowText.value), deny: toList(denyText.value) },
      policy.value?.version ?? 0,
    ),
  onSuccess: () => {
    void qc.invalidateQueries({ queryKey: adminKeys.emailDomainPolicy() })
    toast.success(t('admin.emailDomain.saved'))
  },
  onError: (err) => {
    if (err instanceof ApiError && err.status === 409) {
      // Two different 409s with two different recoveries: a stale version is
      // fixed by reloading, a rollout fence only by an operator transition.
      // Telling an operator to reload into a fence would loop them forever.
      const fenced = err.type.endsWith('email-domain-policy-fenced')
      void qc.invalidateQueries({ queryKey: adminKeys.emailDomainPolicy() })
      toast.error(t(fenced ? 'admin.emailDomain.fenceConflict' : 'admin.emailDomain.staleConflict'))
      return
    }
    if (err instanceof ApiError && err.status === 422) {
      toast.error(t('admin.emailDomain.invalidConflict'))
      return
    }
    if (err instanceof ApiError && err.status === 503) {
      toast.error(t('admin.emailDomain.unavailable'))
      return
    }
    toast.error(t('admin.emailDomain.saveFailed'))
  },
})

// Repeats the button's `:disabled` because pressing Enter inside a text input
// still submits the form in some browsers, and a registration gate is the wrong
// place to rely on the button being the only way in.
function onSubmit(): void {
  if (!editable.value || rejectedEntries.value.length > 0) return
  saveMutation.mutate()
}
</script>
