<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { PlusIcon } from '@heroicons/vue/24/outline'
import {
  SBadge,
  SButton,
  SInput,
  SFormField,
  SToggle,
  SDivider,
} from '@shared/ui'
import type { KeyGroupMember, MemberPatch } from '../api/key-groups'

const props = defineProps<{
  member: KeyGroupMember
  saving: boolean
}>()

const emit = defineEmits<{
  (e: 'save', patch: MemberPatch): void
}>()

const { t } = useI18n()

const form = reactive({
  rotate_on_error_codes: [...props.member.rotation.rotate_on_error_codes],
  rotate_on_token_quota: props.member.rotation.rotate_on_token_quota,
  retry_on_error: props.member.rotation.retry_on_error,
  retry_initial_delay_ms: props.member.rotation.retry_initial_delay_ms,
  retry_multiplier: props.member.rotation.retry_multiplier,
  retry_max_delay_ms: props.member.rotation.retry_max_delay_ms,
  retry_max: props.member.rotation.retry_max,
  retry_jitter_pct: props.member.rotation.retry_jitter_pct,
  max_input_tokens_per_hour: props.member.limits.max_input_tokens_per_hour as number | null,
  max_output_tokens_per_hour: props.member.limits.max_output_tokens_per_hour as number | null,
  max_requests_per_hour: props.member.limits.max_requests_per_hour as number | null,
})

const newCode = ref('')

function addErrorCode() {
  const code = parseInt(newCode.value, 10)
  if (isNaN(code) || code < 100 || code > 599) return
  if (!form.rotate_on_error_codes.includes(code)) {
    form.rotate_on_error_codes.push(code)
  }
  newCode.value = ''
}

function removeErrorCode(code: number) {
  form.rotate_on_error_codes = form.rotate_on_error_codes.filter((c) => c !== code)
}

function parseNullableNumber(val: string): number | null {
  if (val === '') return null
  const n = Number(val)
  return n < 1 ? 1 : n
}

function onSave() {
  emit('save', { ...form })
}
</script>

<template>
  <div class="border border-t-0 border-[var(--color-border)] rounded-b-[var(--radius-md)] bg-[var(--color-surface)] px-6 py-5">
    <h3 class="text-sm font-semibold mb-4">
      {{ t('keys.groups.rotation') }}
    </h3>
    <div class="flex flex-col gap-4">
      <SFormField
        :label="t('keys.groups.errorCodes')"
        name="error-codes"
      >
        <div class="flex flex-wrap items-center gap-1">
          <SBadge
            v-for="code in form.rotate_on_error_codes"
            :key="code"
            variant="neutral"
            removable
            @remove="removeErrorCode(code)"
          >
            {{ code }}
          </SBadge>
          <div class="flex items-center gap-1">
            <SInput
              v-model="newCode"
              type="number"
              size="sm"
              class="w-20"
              :placeholder="t('keys.groups.codeHint')"
              @keydown.enter="addErrorCode"
            />
            <SButton
              variant="ghost"
              icon-only
              size="sm"
              @click="addErrorCode"
            >
              <PlusIcon class="w-4 h-4" />
            </SButton>
          </div>
        </div>
      </SFormField>

      <SFormField
        :label="t('keys.groups.rotateOnQuota')"
        name="rotate-quota"
      >
        <SToggle v-model="form.rotate_on_token_quota" />
      </SFormField>

      <SFormField
        :label="t('keys.groups.retryOnError')"
        name="retry-error"
      >
        <SToggle v-model="form.retry_on_error" />
      </SFormField>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <SFormField
          :label="t('keys.groups.initialDelay')"
          name="initial-delay"
        >
          <SInput
            v-model.number="form.retry_initial_delay_ms"
            type="number"
            size="sm"
          />
        </SFormField>
        <SFormField
          :label="t('keys.groups.multiplier')"
          name="multiplier"
        >
          <SInput
            v-model.number="form.retry_multiplier"
            type="number"
            size="sm"
          />
        </SFormField>
        <SFormField
          :label="t('keys.groups.maxDelay')"
          name="max-delay"
        >
          <SInput
            v-model.number="form.retry_max_delay_ms"
            type="number"
            size="sm"
          />
        </SFormField>
        <SFormField
          :label="t('keys.groups.maxRetries')"
          name="max-retries"
        >
          <SInput
            v-model.number="form.retry_max"
            type="number"
            size="sm"
          />
        </SFormField>
        <SFormField
          :label="t('keys.groups.jitter')"
          name="jitter"
        >
          <SInput
            v-model.number="form.retry_jitter_pct"
            type="number"
            size="sm"
          />
        </SFormField>
      </div>
    </div>

    <SDivider class="my-5" />

    <h3 class="text-sm font-semibold mb-4">
      {{ t('keys.groups.hourlyLimits') }}
    </h3>
    <div class="flex flex-col gap-4">
      <SFormField
        :label="t('keys.groups.maxInputTokens')"
        name="max-input"
        :help="t('keys.groups.limitHelp')"
      >
        <SInput
          :model-value="form.max_input_tokens_per_hour ?? ''"
          type="number"
          min="1"
          size="sm"
          @update:model-value="form.max_input_tokens_per_hour = parseNullableNumber(String($event))"
        />
      </SFormField>
      <SFormField
        :label="t('keys.groups.maxOutputTokens')"
        name="max-output"
        :help="t('keys.groups.limitHelp')"
      >
        <SInput
          :model-value="form.max_output_tokens_per_hour ?? ''"
          type="number"
          min="1"
          size="sm"
          @update:model-value="form.max_output_tokens_per_hour = parseNullableNumber(String($event))"
        />
      </SFormField>
      <SFormField
        :label="t('keys.groups.maxRequests')"
        name="max-requests"
        :help="t('keys.groups.limitHelp')"
      >
        <SInput
          :model-value="form.max_requests_per_hour ?? ''"
          type="number"
          min="1"
          size="sm"
          @update:model-value="form.max_requests_per_hour = parseNullableNumber(String($event))"
        />
      </SFormField>
    </div>

    <div class="flex justify-end mt-5">
      <SButton
        variant="primary"
        size="sm"
        :loading="saving"
        @click="onSave"
      >
        {{ t('app.save') }}
      </SButton>
    </div>
  </div>
</template>
