<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getActivityPlugin } from '../plugins'
import { InProcessBridge, type HostBridge } from '../sdk/bridge'
import type { ActivityTeardown, JSONSchema } from '../sdk/types'
import { useActivityHost } from '../composables/useActivityHost'
import type { ActivityType } from '../types'
import SchemaForm from './SchemaForm.vue'
import ActivityOutcomeBadge from './ActivityOutcomeBadge.vue'

const props = withDefaults(
  defineProps<{
    chatroomId: string
    activityType: ActivityType
    sessionId?: string | null
    subjectUserId?: string | null
    /** Injectable for tests; production always uses the trusted in-process bridge. */
    bridge?: HostBridge
  }>(),
  {
    sessionId: null,
    subjectUserId: null,
    bridge: () => new InProcessBridge(),
  },
)

const { t } = useI18n()

const { submit, submitting, errorMessage, outcome } = useActivityHost({
  chatroomId: () => props.chatroomId,
  activityTypeId: () => props.activityType.id,
  sessionId: () => props.sessionId ?? null,
  subjectUserId: () => props.subjectUserId ?? null,
})

// Form-path submit handler: `submit` re-throws so a plugin's `emit` can react to
// the failure, but as a bare form handler that rejection would float. Swallow it
// here — the error is already surfaced via `errorMessage` inside `submit`.
function onFormSubmit(payload: Record<string, unknown>): void {
  void submit(payload).catch(() => {})
}

const plugin = computed(() => getActivityPlugin(props.activityType.key))
const schema = computed<JSONSchema>(
  () => (plugin.value?.schema ?? props.activityType.payload_schema) as JSONSchema,
)

// ---- plugin path: mount through the bridge -----------------------------------

const pluginContainer = ref<HTMLElement | null>(null)
let teardown: ActivityTeardown | null = null

onMounted(() => {
  if (!plugin.value || !pluginContainer.value) return
  teardown = props.bridge.mount(plugin.value, {
    container: pluginContainer.value,
    schema: schema.value,
    session: {
      activityTypeKey: props.activityType.key,
      sessionId: props.sessionId ?? null,
    },
    t: (key, named) => t(key, named ?? {}),
    submit,
  })
})

onBeforeUnmount(() => {
  teardown?.()
  teardown = null
})
</script>

<template>
  <section class="activity-host">
    <!-- Plugin path (trusted, in-process). The plugin renders into this host-owned node. -->
    <div
      v-if="plugin"
      ref="pluginContainer"
      class="activity-host__plugin"
    />
    <!-- Fallback path: generic schema-derived form. -->
    <SchemaForm
      v-else
      :schema="schema"
      :submitting="submitting"
      @submit="onFormSubmit"
    />

    <p
      v-if="errorMessage"
      class="activity-host__error"
      role="alert"
    >
      {{ errorMessage }}
    </p>

    <div
      v-if="outcome"
      class="activity-host__outcome"
    >
      <span class="activity-host__outcome-label">{{ $t('activities.host.outcome') }}</span>
      <ActivityOutcomeBadge
        :status="outcome.status"
        :is-valid="outcome.isValid"
      />
    </div>
  </section>
</template>

<style scoped>
.activity-host {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.activity-host__error {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-danger);
}
.activity-host__outcome {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.activity-host__outcome-label {
  font-size: 0.75rem;
  color: var(--color-muted);
}
</style>
