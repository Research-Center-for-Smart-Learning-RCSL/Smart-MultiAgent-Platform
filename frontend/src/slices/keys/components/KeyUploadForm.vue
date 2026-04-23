<script setup lang="ts">
import { computed } from 'vue'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { CAPABILITIES, type ApiKeyProvider } from '../api/keys'

const providerOptions = computed(() =>
  (Object.keys(CAPABILITIES) as ApiKeyProvider[]).map((p) => ({
    value: p,
    caps: CAPABILITIES[p].join(', '),
  })),
)

const emit = defineEmits<{
  (e: 'submit', payload: { provider: ApiKeyProvider; name: string; secret: string }): void
}>()

const schema = toTypedSchema(
  z.object({
    provider: z.enum(['claude', 'openai', 'gemini', 'voyage', 'cohere']),
    name: z.string().trim().min(1).max(200),
    secret: z.string().trim().min(1).max(4096),
  }),
)

const { handleSubmit, errors, defineField, resetForm } = useForm({
  validationSchema: schema,
  initialValues: { provider: 'openai' as ApiKeyProvider, name: '', secret: '' },
})
const [provider] = defineField('provider')
const [name] = defineField('name')
const [secret] = defineField('secret')

const onSubmit = handleSubmit((values) => {
  emit('submit', values as { provider: ApiKeyProvider; name: string; secret: string })
  resetForm()
})
</script>

<template>
  <form class="key-upload-form" @submit.prevent="onSubmit">
    <label>
      {{ $t('keys.form.provider') }}
      <select v-model="provider" data-testid="key-provider">
        <option v-for="opt in providerOptions" :key="opt.value" :value="opt.value">
          {{ opt.value }} ({{ opt.caps }})
        </option>
      </select>
    </label>
    <label>
      {{ $t('keys.form.name') }}
      <input v-model="name" data-testid="key-name" :placeholder="$t('keys.form.namePlaceholder')" />
      <small v-if="errors.name">{{ errors.name }}</small>
    </label>
    <label>
      {{ $t('keys.form.secret') }}
      <!-- type=password keeps autofill scoped and the input masked. -->
      <input v-model="secret" type="password" autocomplete="off" data-testid="key-secret" />
      <small v-if="errors.secret">{{ errors.secret }}</small>
    </label>
    <button type="submit" data-testid="key-upload-submit">{{ $t('keys.form.submit') }}</button>
  </form>
</template>
