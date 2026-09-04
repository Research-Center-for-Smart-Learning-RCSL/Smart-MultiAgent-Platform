<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { UserIcon, UserGroupIcon, XCircleIcon } from '@heroicons/vue/24/outline'
import { SAuthCard, SButton, SFormField, SInput, SLoadingSpinner } from '@shared/ui'
import { ApiError, RateLimitError } from '@shared/errors'
import { setAccessToken, setGuestContext } from '@shared/transport'
import { useSessionStore } from '@shared/stores/session'
import { createGuestSession, enrollGuest } from '../api'
import { GUEST_STORAGE_PREFIX, useGuestSessionStore } from '../stores/guestSession'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const session = useSessionStore()
const guestSessionStore = useGuestSessionStore()
const chatroomId = route.params.chatroomId as string
const guestToken = route.params.guestToken as string

interface StoredGuest {
  browser_id: string
  guest_session_id: string
  display_name: string
}

type ViewState =
  | 'idle'
  | 'choosing'
  | 'resuming'
  | 'enrolling'
  | 'invalid'
  | 'error'
  | 'cap_reached'

const state = ref<ViewState>('idle')
const resumedName = ref('')
const enterAsGuest = ref(false)

const accountDisplayName = computed(
  () => session.me?.display_name ?? session.me?.email ?? '',
)

function readStored(): StoredGuest | null {
  try {
    const raw = localStorage.getItem(`${GUEST_STORAGE_PREFIX}${chatroomId}`)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<StoredGuest>
    if (parsed.browser_id && parsed.display_name) return parsed as StoredGuest
    return null
  } catch {
    return null
  }
}

function writeStored(data: StoredGuest): void {
  try {
    localStorage.setItem(`${GUEST_STORAGE_PREFIX}${chatroomId}`, JSON.stringify(data))
  } catch {
    // localStorage unavailable -- non-fatal
  }
}

const schema = toTypedSchema(
  z.object({
    displayName: z.string().trim().min(1).max(100),
  }),
)

const { handleSubmit, errors, defineField } = useForm({
  validationSchema: schema,
  initialValues: { displayName: '' },
})

const [displayName] = defineField('displayName')
const displayNameModel = computed({
  get: () => displayName.value ?? '',
  set: (v: string) => {
    displayName.value = v
  },
})

function classifyError(e: unknown): ViewState {
  if (e instanceof RateLimitError) return 'cap_reached'
  if (e instanceof ApiError && e.status === 429) return 'cap_reached'
  if (e instanceof ApiError && [401, 403, 404].includes(e.status)) return 'invalid'
  return 'error'
}

async function enterChatroom(
  accessToken: string,
  guestSessionId: string,
  name: string,
  browserId: string,
): Promise<void> {
  setAccessToken(accessToken)
  setGuestContext(chatroomId)
  guestSessionStore.setGuestToken(chatroomId, guestToken)
  writeStored({ browser_id: browserId, guest_session_id: guestSessionId, display_name: name })

  // Strip the token from the URL (R24.43) before navigating to the room.
  history.replaceState(null, '', `/c/${chatroomId}`)
  await router.replace({
    name: 'conversation.chatroom',
    params: { chatroomId },
  })
}

async function enrollRegisteredGuest(name?: string): Promise<void> {
  await enrollGuest(chatroomId, guestToken, name)
  history.replaceState(null, '', `/c/${chatroomId}`)
  await router.replace({ name: 'conversation.chatroom', params: { chatroomId } })
}

const doEnroll = handleSubmit(async (values) => {
  state.value = 'enrolling'
  try {
    if (session.isAuthenticated && !enterAsGuest.value) {
      await enrollRegisteredGuest(values.displayName)
      return
    }
    const stored = readStored()
    const browserId = stored?.browser_id ?? crypto.randomUUID()
    const result = await createGuestSession(
      chatroomId,
      guestToken,
      values.displayName,
      browserId,
    )
    await enterChatroom(result.access_token, result.guest_session_id, result.display_name, browserId)
  } catch (e) {
    state.value = classifyError(e)
  }
})

async function doResume(): Promise<void> {
  state.value = 'enrolling'
  const stored = readStored()
  if (!stored) {
    state.value = 'idle'
    return
  }
  try {
    const result = await createGuestSession(
      chatroomId,
      guestToken,
      stored.display_name,
      stored.browser_id,
    )
    await enterChatroom(result.access_token, result.guest_session_id, result.display_name, stored.browser_id)
  } catch (e) {
    state.value = classifyError(e)
  }
}

function switchToChangeName(): void {
  displayName.value = resumedName.value
  state.value = 'idle'
}

function chooseGuest(): void {
  enterAsGuest.value = true
  state.value = 'idle'
}

async function chooseOwnAccount(): Promise<void> {
  state.value = 'enrolling'
  try {
    await enrollRegisteredGuest(accountDisplayName.value || undefined)
  } catch (e) {
    state.value = classifyError(e)
  }
}

onMounted(() => {
  if (session.isAuthenticated) {
    state.value = 'choosing'
    return
  }
  const stored = readStored()
  if (stored) {
    resumedName.value = stored.display_name
    state.value = 'resuming'
  }
})
</script>

<template>
  <SAuthCard
    class="guest-landing"
    :title="t('conversation.guest.title')"
  >
    <div
      class="guest-content"
      aria-live="polite"
    >
      <!-- Logged-in user choice (AC-23) -->
      <template v-if="state === 'choosing'">
        <p class="guest-desc">
          {{ t('conversation.guest.chooseEntry') }}
        </p>
        <div class="guest-choice">
          <button
            class="choice-card"
            @click="chooseOwnAccount"
          >
            <UserIcon
              class="choice-card__icon"
              aria-hidden="true"
            />
            <span class="choice-card__label">
              {{ t('conversation.guest.enterAsUser', { name: accountDisplayName }) }}
            </span>
          </button>
          <button
            class="choice-card"
            @click="chooseGuest"
          >
            <UserGroupIcon
              class="choice-card__icon"
              aria-hidden="true"
            />
            <span class="choice-card__label">
              {{ t('conversation.guest.enterAsGuest') }}
            </span>
          </button>
        </div>
      </template>

      <!-- Returning guest: welcome back -->
      <template v-else-if="state === 'resuming'">
        <p class="guest-desc">
          {{ t('conversation.guest.welcomeBack', { name: resumedName }) }}
        </p>
        <div class="guest-actions">
          <SButton
            variant="primary"
            class="state-action"
            @click="doResume"
          >
            {{ t('conversation.guest.enterChatroom') }}
          </SButton>
          <SButton
            variant="ghost"
            class="state-action"
            @click="switchToChangeName"
          >
            {{ t('conversation.guest.changeName') }}
          </SButton>
        </div>
      </template>

      <!-- New guest or name change: display name form -->
      <template v-else-if="state === 'idle'">
        <p class="guest-desc">
          {{ t('conversation.guest.description') }}
        </p>
        <form
          class="guest-form"
          @submit.prevent="doEnroll"
        >
          <SFormField
            :label="t('conversation.guest.displayName')"
            name="displayName"
            v-bind="errors.displayName ? { error: errors.displayName } : {}"
            required
          >
            <SInput
              v-model="displayNameModel"
              :maxlength="100"
              :placeholder="t('conversation.guest.displayNamePlaceholder')"
              :error="!!errors.displayName"
            />
          </SFormField>
          <SButton
            type="submit"
            variant="primary"
            class="state-action"
            :disabled="!displayNameModel.trim()"
          >
            {{ t('conversation.guest.enterChatroom') }}
          </SButton>
        </form>
      </template>

      <!-- Enrolling -->
      <template v-else-if="state === 'enrolling'">
        <SLoadingSpinner
          size="md"
          :text="t('conversation.guest.enrolling')"
        />
      </template>

      <!-- Guest cap reached (429) -->
      <template v-else-if="state === 'cap_reached'">
        <XCircleIcon
          class="state-icon state-icon--failure"
          aria-hidden="true"
        />
        <p
          class="state-text"
          role="alert"
        >
          {{ t('conversation.guest.capReached') }}
        </p>
      </template>

      <!-- Invalid token (401/403/404) -->
      <template v-else-if="state === 'invalid'">
        <XCircleIcon
          class="state-icon state-icon--failure"
          aria-hidden="true"
        />
        <p
          class="state-text"
          role="alert"
        >
          {{ t('conversation.guest.invalidToken') }}
        </p>
      </template>

      <!-- Transient error (retryable) -->
      <template v-else>
        <XCircleIcon
          class="state-icon state-icon--failure"
          aria-hidden="true"
        />
        <p
          class="state-text"
          role="alert"
        >
          {{ t('conversation.guest.networkError') }}
        </p>
        <SButton
          variant="primary"
          class="state-action"
          @click="doEnroll"
        >
          {{ t('conversation.guest.retry') }}
        </SButton>
      </template>
    </div>
  </SAuthCard>
</template>

<style scoped>
.guest-landing :deep(.s-auth-card__title) {
  text-align: center;
}

.guest-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--space-4);
  padding: var(--space-4) 0;
}

.state-icon {
  width: 48px;
  height: 48px;
}

.state-icon--failure {
  color: var(--color-danger);
}

.state-text {
  font-size: var(--font-size-sm);
  color: var(--color-fg);
  margin: 0;
}

.guest-desc {
  font-size: var(--font-size-sm);
  color: var(--color-muted);
  margin: 0;
}

.guest-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  width: 100%;
}

.guest-actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 100%;
}

.state-action {
  margin-top: var(--space-2);
}

.guest-choice {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  width: 100%;
}

.choice-card {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--transition-fast), background var(--transition-fast);
}

.choice-card:hover {
  border-color: var(--color-accent);
  background: var(--color-surface-hover);
}

.choice-card__icon {
  width: 24px;
  height: 24px;
  color: var(--color-muted);
  flex-shrink: 0;
}

.choice-card__label {
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
}
</style>
