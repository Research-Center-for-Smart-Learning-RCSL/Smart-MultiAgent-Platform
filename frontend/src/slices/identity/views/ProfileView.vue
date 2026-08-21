<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { SPageHeader, SCard, SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import { authApi, type Identity } from '../api/auth'
import { useSessionStore } from '../stores/session'
import { displayNameSchema, DISPLAY_NAME_MAX_LENGTH, validateField, errorAttrs } from '../validation'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const toast = useToast()

const displayName = ref('')
const serverError = ref<string | null>(null)
const submitting = ref(false)
const fieldErrors = ref<Record<string, string | undefined>>({})
const inputRef = ref<InstanceType<typeof SInput> | null>(null)

// --- Connections (Google linking, R6.17) ---
const identities = ref<Identity[]>([])
const connectionsError = ref<string | null>(null)
const connectionsFlash = ref<string | null>(null)
const lastCredentialError = ref(false)
const linking = ref(false)
const unlinking = ref(false)
const googleIdentity = computed(() => identities.value.find((i) => i.provider === 'google') ?? null)

async function loadIdentities(): Promise<void> {
  try {
    identities.value = await authApi.listIdentities()
  } catch {
    connectionsError.value = t('identity.profile.connections.loadError')
  }
}

function initConnectionFlash(): void {
  if (route.query.oauth_linked === '1') {
    connectionsFlash.value = t('identity.profile.connections.linkedSuccess')
  } else if (route.query.oauth_error) {
    const code = route.query.oauth_error as string
    connectionsError.value =
      code === 'oauth-identity-conflict'
        ? t('identity.profile.connections.conflictError')
        : t('identity.profile.connections.linkError')
  }
  if (route.query.oauth_linked || route.query.oauth_error) {
    const { oauth_linked: _l, oauth_error: _e, ...rest } = route.query
    router.replace({ ...route, query: rest })
  }
}

async function linkGoogle(): Promise<void> {
  connectionsError.value = null
  lastCredentialError.value = false
  linking.value = true
  try {
    const { authorize_url } = await authApi.googleLinkStart()
    window.location.assign(authorize_url)
  } catch {
    connectionsError.value = t('identity.profile.connections.linkError')
    linking.value = false
  }
}

async function unlinkGoogle(): Promise<void> {
  connectionsError.value = null
  lastCredentialError.value = false
  unlinking.value = true
  try {
    await authApi.googleUnlink()
    await loadIdentities()
  } catch (e: unknown) {
    if (isProblemWithType(e, '/auth/last-credential')) {
      lastCredentialError.value = true
    } else {
      connectionsError.value = t('identity.profile.connections.unlinkError')
    }
  } finally {
    unlinking.value = false
  }
}

onMounted(async () => {
  displayName.value = session.me?.display_name ?? ''
  initConnectionFlash()
  await loadIdentities()
  await nextTick()
  inputRef.value?.$el?.querySelector('input')?.focus()
})

function validateDisplayName(): boolean {
  return validateField(displayNameSchema, displayName.value, fieldErrors, 'displayName', t)
}

async function submit(): Promise<void> {
  serverError.value = null
  if (!validateDisplayName()) return

  submitting.value = true
  try {
    const trimmed = displayName.value.trim()
    const updated = await authApi.updateProfile({ display_name: trimmed || null })
    // Reflect the server-normalised value (control chars stripped, re-trimmed).
    session.setMe(updated)
    displayName.value = updated.display_name ?? ''
    toast.success(t('identity.profile.saved'))
  } catch {
    serverError.value = t('identity.errors.generic')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div>
    <SPageHeader :title="$t('identity.profile.title')" />

    <SCard class="form-card">
      <dl class="current-email">
        <dt>{{ $t('identity.profile.emailLabel') }}</dt>
        <dd>{{ session.me?.email }}</dd>
      </dl>

      <form
        class="form-stack"
        @submit.prevent="submit"
      >
        <SFormField
          :label="$t('identity.profile.displayName')"
          name="displayName"
          v-bind="errorAttrs(fieldErrors.displayName)"
          :help="$t('identity.profile.displayNameHelp')"
        >
          <SInput
            ref="inputRef"
            v-model="displayName"
            type="text"
            autocomplete="nickname"
            :maxlength="DISPLAY_NAME_MAX_LENGTH"
            :disabled="submitting"
            :error="!!fieldErrors.displayName"
            @blur="validateDisplayName"
          />
        </SFormField>

        <SAlert
          v-if="serverError"
          variant="danger"
          focus-on-mount
        >
          {{ serverError }}
        </SAlert>

        <SButton
          type="submit"
          variant="primary"
          size="md"
          :loading="submitting"
          :disabled="submitting"
          :aria-busy="submitting"
          class="submit-btn"
        >
          {{ $t('identity.profile.submit') }}
        </SButton>
      </form>
    </SCard>

    <SCard class="form-card connections-card">
      <h2 class="section-title">
        {{ $t('identity.profile.connections.title') }}
      </h2>

      <SAlert
        v-if="connectionsFlash"
        variant="success"
        dismissible
        role="status"
        @dismiss="connectionsFlash = null"
      >
        {{ connectionsFlash }}
      </SAlert>

      <SAlert
        v-if="connectionsError"
        variant="danger"
      >
        {{ connectionsError }}
      </SAlert>

      <SAlert
        v-if="lastCredentialError"
        variant="warning"
      >
        {{ $t('identity.profile.connections.setPasswordFirst') }}
        <RouterLink :to="{ name: 'identity.passwordResetRequest' }">
          {{ $t('identity.profile.connections.setPasswordLink') }}
        </RouterLink>
      </SAlert>

      <div class="provider-row">
        <div class="provider-info">
          <span class="provider-name">{{ $t('identity.profile.connections.google') }}</span>
          <span
            v-if="googleIdentity"
            class="provider-status"
          >
            {{ $t('identity.profile.connections.linkedAs', { email: googleIdentity.email ?? '' }) }}
          </span>
          <span
            v-else
            class="provider-status"
          >
            {{ $t('identity.profile.connections.notLinked') }}
          </span>
        </div>

        <SButton
          v-if="googleIdentity"
          type="button"
          variant="secondary"
          size="sm"
          :loading="unlinking"
          :disabled="unlinking"
          @click="unlinkGoogle"
        >
          {{ $t('identity.profile.connections.unlink') }}
        </SButton>
        <SButton
          v-else
          type="button"
          variant="secondary"
          size="sm"
          :loading="linking"
          :disabled="linking"
          @click="linkGoogle"
        >
          {{ $t('identity.profile.connections.link') }}
        </SButton>
      </div>
    </SCard>
  </div>
</template>

<style scoped>
.form-card {
  max-width: 480px;
}

.current-email {
  margin: 0 0 var(--space-5);
}

.current-email dt {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  margin-bottom: var(--space-0-5);
}

.current-email dd {
  font-size: var(--font-size-sm);
  color: var(--color-fg);
  margin: 0;
}

.form-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.submit-btn {
  width: 100%;
}

.connections-card {
  margin-top: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.section-title {
  font-size: var(--font-size-lg);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin: 0;
}

.provider-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}

.provider-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-0-5);
}

.provider-name {
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
}

.provider-status {
  font-size: var(--font-size-code);
  color: var(--color-muted);
}

@media (max-width: 767px) {
  .form-card {
    max-width: none;
  }
}
</style>
