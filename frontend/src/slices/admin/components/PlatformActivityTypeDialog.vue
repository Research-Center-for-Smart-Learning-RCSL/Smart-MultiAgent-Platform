<template>
  <SModal
    :open="open"
    :title="$t('admin.activities.examples.editTitle')"
    size="md"
    @close="emit('close')"
  >
    <form
      class="flex flex-col gap-4"
      @submit.prevent="onSubmit"
    >
      <p class="text-xs text-[var(--color-muted)]">
        {{ $t('admin.activities.examples.editHelp') }}
      </p>

      <SAlert
        v-if="refusal !== null"
        variant="warning"
        :focus-on-mount="true"
        data-testid="platform-type-refusal"
      >
        {{ refusal }}
      </SAlert>

      <SFormField
        :label="$t('admin.activities.examples.fieldName')"
        name="platform-type-name"
      >
        <SInput
          id="platform-type-name"
          v-model="form.name"
          data-testid="platform-type-name"
        />
      </SFormField>

      <SFormField
        :label="$t('admin.activities.examples.fieldRetention')"
        name="platform-type-retention"
        :help="$t('admin.activities.examples.fieldRetentionHint')"
      >
        <SInput
          id="platform-type-retention"
          v-model="retention"
          data-testid="platform-type-retention"
        />
      </SFormField>

      <SCheckbox
        v-model="form.expose_payload_to_agent"
        data-testid="platform-type-expose"
      >
        {{ $t('admin.activities.examples.fieldExpose') }}
      </SCheckbox>
      <SCheckbox
        v-model="form.echo_includes_content"
        data-testid="platform-type-echo"
      >
        {{ $t('admin.activities.examples.fieldEcho') }}
      </SCheckbox>

      <p
        v-if="retentionInvalid"
        class="text-xs text-[var(--color-danger)]"
        role="alert"
        data-testid="platform-type-retention-invalid"
      >
        {{ $t('admin.activities.examples.retentionInvalid') }}
      </p>

      <div class="flex items-center gap-3">
        <SButton
          variant="primary"
          type="submit"
          :loading="saveMutation.isPending.value"
          :disabled="retentionInvalid || form.name.trim() === ''"
          data-testid="platform-type-save"
        >
          {{ $t('admin.activities.examples.save') }}
        </SButton>
        <SButton
          variant="secondary"
          type="button"
          @click="emit('close')"
        >
          {{ $t('admin.common.cancel') }}
        </SButton>
      </div>
    </form>
  </SModal>
</template>

<script setup lang="ts">
// Edit an installed example's safe and governance fields ([R30.23], Q-4).
//
// Four fields and no more. This exists because the alternative is a dead end:
// both shipped examples set `expose_payload_to_agent`, so an admin who locks that
// flag to false blocks them at activation with nobody able to fix it. The fix is
// that the owner of the row can edit the row -- not that the check is skipped.
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMutation, useQueryClient } from '@tanstack/vue-query'

import { SAlert, SButton, SCheckbox, SFormField, SInput, SModal } from '@shared/ui'
import { useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'

import { adminApi } from '../api/admin'
import { adminKeys } from '../queries'
import type { AdminActivityTypeRow, AdminPlatformActivityTypeInput } from '../types'

// The row is passed in rather than looked up here. The catalogue listing carries
// the *shipped* values, which stop being the truth the moment an admin edits one
// ([R30.23] Q-4), so the form seeds from the stored row -- and taking it as a
// prop keeps that the parent's concern rather than this dialog reaching into a
// cache that may not be populated yet.
const props = defineProps<{ open: boolean; row: AdminActivityTypeRow | null }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'saved'): void }>()

const { t } = useI18n()
const toast = useToast()
const qc = useQueryClient()

const form = reactive<AdminPlatformActivityTypeInput>({
  name: '',
  retention_days: null,
  expose_payload_to_agent: true,
  echo_includes_content: false,
})

/** Bound as text so an empty box means "unset" rather than 0, matching the
 *  policy form's handling of the same field. */
const retention = ref('')
const refusal = ref<string | null>(null)

const retentionInvalid = computed(() => {
  const raw = retention.value.trim()
  if (raw === '') return false
  const n = Number(raw)
  return !Number.isInteger(n) || n < 1
})

// Keyed on the row's *id*, not the row object. `refetchOnWindowFocus` is left at
// its default, so alt-tabbing away and back re-runs the types query and hands
// this a new object with identical contents — watching identity would reseed the
// form there and silently discard whatever the admin had typed. The id changes
// only when a different type is opened, which is exactly when reseeding is right.
watch(
  () => [props.open, props.row?.id ?? null] as const,
  () => {
    if (!props.open) return
    refusal.value = null
    const row = props.row
    form.name = row?.name ?? ''
    form.expose_payload_to_agent = row?.expose_payload_to_agent ?? true
    form.echo_includes_content = row?.echo_includes_content ?? false
    retention.value = row?.retention_days == null ? '' : String(row.retention_days)
  },
  { immediate: true },
)

/** Wire field name -> the label this surface already uses for that switch. The
 *  backend names `expose_payload_to_agent`, which is right for an API and wrong
 *  to drop into a sentence a zh-TW reader has to act on. */
const FIELD_LABEL_KEYS: Readonly<Record<string, string>> = {
  expose_payload_to_agent: 'admin.activities.examples.fieldExpose',
  echo_includes_content: 'admin.activities.examples.fieldEcho',
  retention_days: 'admin.activities.examples.fieldRetention',
}

/** The policy refusal ([R30.30]) is what Q-4 exists for, so it has to say which
 *  field refused rather than surfacing as a bare 409. Checked before the generic
 *  error branch -- the ordering is load-bearing, as in ActivityTypeForm. */
function refusalMessage(err: unknown): string | null {
  if (!(err instanceof ApiError) || !err.type.includes('type-violates-policy')) return null
  const field = err.extra.field
  const key = typeof field === 'string' ? FIELD_LABEL_KEYS[field] : undefined
  return key
    ? t('admin.activities.examples.policyRefusedField', { field: t(key) })
    : t('admin.activities.examples.policyRefused')
}

const saveMutation = useMutation({
  mutationFn: (body: AdminPlatformActivityTypeInput) =>
    adminApi.updatePlatformActivityType(props.row?.id ?? '', body),
  onSuccess: () => {
    void qc.invalidateQueries({ queryKey: adminKeys.activityTypes() })
    toast.success(t('admin.activities.examples.saved'))
    emit('saved')
  },
  onError: (err: unknown) => {
    const refused = refusalMessage(err)
    if (refused !== null) {
      refusal.value = refused
      return
    }
    toast.error(t('admin.activities.examples.saveFailed'))
  },
})

function onSubmit(): void {
  if (retentionInvalid.value || props.row === null) return
  refusal.value = null
  const raw = retention.value.trim()
  saveMutation.mutate({
    name: form.name.trim(),
    retention_days: raw === '' ? null : Number(raw),
    expose_payload_to_agent: form.expose_payload_to_agent,
    echo_includes_content: form.echo_includes_content,
  })
}
</script>
