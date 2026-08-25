<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getActivityPlugin } from '../plugins'
import { InProcessBridge, type HostBridge } from '../sdk/bridge'
import type { ActivitySubmissionResult, ActivityTeardown, JSONSchema } from '../sdk/types'
import { useActivityHost } from '../composables/useActivityHost'
import { useDraftThrottle } from '../composables/useDraftThrottle'
import type { ActivityTypePublic } from '../types'
import SchemaForm from './SchemaForm.vue'
import ActivityOutcomeBadge from './ActivityOutcomeBadge.vue'

const props = withDefaults(
  defineProps<{
    chatroomId: string
    activityType: ActivityTypePublic
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

const emit = defineEmits<{
  /** A submission was accepted by the server. The host owns no state that
   *  depends on it; the panel does — answering again retracts an "I am
   *  finished" declaration server-side ([R30.22]), so a listener that shows that
   *  declaration has to hear about it or it goes stale. */
  submitted: []
  /** This worksheet's current unsent contents, throttled ([R32.01]).
   *
   *  Emitted *upward* and never sent from here. The activities slice must not
   *  touch the chatroom socket — gate #1's `SLICE_DEPS` makes `conversation` the
   *  host that imports `activities` one-way, so a send from this side would be a
   *  boundary violation as well as a layering one. `ChatroomView` owns the send,
   *  the same way it already owns `typing.start` on the composer's behalf. */
  draft: [payload: unknown]
  /** Retract this worksheet's draft: on a successful submit, and on unmount. */
  draftClear: []
}>()

const { t } = useI18n()

const { submit: rawSubmit, submitting, errorMessage, outcome } = useActivityHost({
  chatroomId: () => props.chatroomId,
  activityTypeId: () => props.activityType.id,
  sessionId: () => props.sessionId ?? null,
  subjectUserId: () => props.subjectUserId ?? null,
})

// ---- draft reporting ([R32.01]) ---------------------------------------------
// The window and its cancel-before-clear rule live in `useDraftThrottle`; the
// group-proposal form in `ActivityPanel` needs the same behaviour and does not
// pass through this component.

const { report: reportDraft, cancel: cancelPendingDraft } = useDraftThrottle<unknown>(
  (payload) => emit('draft', payload),
)

/** The single submit both paths (plugin and schema form) go through, so neither
 *  can accept a submission without announcing it. Only a resolved call emits:
 *  `rawSubmit` re-throws, and a failed submission changed nothing server-side. */
async function submit(payload: unknown): Promise<ActivitySubmissionResult> {
  const result = await rawSubmit(payload)
  // Ordered before `submitted` so the retraction goes out with the same
  // certainty the submission did: the answer is now a submission, and a
  // submission is governed by its own consent rules rather than by this one.
  cancelPendingDraft()
  emit('draftClear')
  emit('submitted')
  return result
}

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
    reportDraft,
  })
})

onBeforeUnmount(() => {
  teardown?.()
  teardown = null
  // AC-4's unmount half. The timer must not outlive the component — it would fire
  // into a room whose panel is gone — and the retraction has to go out even though
  // nothing was submitted: leaving on the tab is not the same as sending.
  cancelPendingDraft()
  emit('draftClear')
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
      @change="reportDraft"
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
  gap: var(--space-3);
}

/* The containment context a plugin lays out against (R30.34). Declared by the
   host rather than inside a plugin, so "fit the surface you were given" is a
   property of the host contract — which is what the isolating iframe host of
   R30.19 needs, where a viewport breakpoint would measure the iframe. A plugin
   whose host declares no container simply never matches its container queries
   and degrades to its narrow layout, which is the safe direction. */
.activity-host__plugin {
  container-type: inline-size;
}
.activity-host__error {
  margin: 0;
  font-size: var(--font-size-code);
  color: var(--color-danger);
}
.activity-host__outcome {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.activity-host__outcome-label {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
}
</style>
