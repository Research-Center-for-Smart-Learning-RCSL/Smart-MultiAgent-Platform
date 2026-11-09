<script setup lang="ts">
// "Design classroom agents" ([R34.08]): the owner fills a form, the project's
// design agent drafts teacher and peer prompts, and the owner compares them
// with the current ones before applying both at once. Nothing is written to an
// agent until Apply, and Apply is refused if either agent changed meanwhile.
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import { useForm } from 'vee-validate'
import { z } from 'zod'
import { SparklesIcon } from '@heroicons/vue/24/outline'
import {
  SAlert,
  SBadge,
  SButton,
  SCard,
  SFormField,
  SLoadingSpinner,
  SQueryError,
  SSelect,
  STextarea,
} from '@shared/ui'
import { useConfirmDialog, useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import { toTypedSchema } from '@shared/utils/zodTypedSchema'
import type { DesignRunIn, DesignTargetOut } from '@shared/api-client'
import { agentKeys, agentsApi } from '@slices/agents'
import { activityKeys, listActivityTypes } from '@slices/activities'
import { useClassroomDesign } from '../composables/useClassroomDesign'

const props = defineProps<{
  projectId: string
}>()

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirmDialog()

const NOTES_MAX = 2000
const DIFFICULTIES = ['introductory', 'intermediate', 'advanced'] as const
const ERROR_PATTERNS = ['none', 'one_in_three'] as const
const TONES = ['warm', 'neutral', 'concise'] as const
const ROUNDS = [1, 2, 3, 4, 5, 6] as const

const agentsQuery = useQuery({
  queryKey: computed(() => agentKeys.agents(props.projectId)),
  queryFn: () => agentsApi.list(props.projectId),
})
const typesQuery = useQuery({
  queryKey: computed(() => activityKeys.types(props.projectId)),
  queryFn: () => listActivityTypes(props.projectId),
})
const agentOptions = computed(() =>
  (agentsQuery.data.value ?? []).map((a) => ({ value: a.id, label: a.name })),
)
const typeOptions = computed(() =>
  (typesQuery.data.value ?? []).map((ty) => ({ value: ty.id, label: ty.name })),
)
const agentName = (id: string): string =>
  agentsQuery.data.value?.find((a) => a.id === id)?.name ?? t('dashboard.alerts.unknownAgent')

const design = useClassroomDesign(() => props.projectId)
const { run, inFlight, conflict } = design
// The owner chose to start over while a proposal is on screen.
const startingOver = ref(false)
const showForm = computed(
  () => !inFlight.value && (!run.value || run.value.status !== 'succeeded' || startingOver.value || conflict.value),
)

const schema = computed(() =>
  toTypedSchema(
    z
      .object({
        design_agent_id: z.string().min(1, t('dashboard.design.required')),
        teacher_agent_id: z.string().min(1, t('dashboard.design.required')),
        peer_agent_id: z.string().min(1, t('dashboard.design.required')),
        activity_type_id: z.string().min(1, t('dashboard.design.required')),
        difficulty: z.enum(DIFFICULTIES),
        rounds: z.number().int().min(1).max(6),
        peer_error_pattern: z.enum(ERROR_PATTERNS),
        tone: z.enum(TONES),
        notes: z.string().max(NOTES_MAX),
      })
      .refine((v) => v.teacher_agent_id !== v.peer_agent_id, {
        path: ['peer_agent_id'],
        message: t('dashboard.design.sameAgent'),
      }),
  ),
)
const { handleSubmit, errors, defineField } = useForm<DesignRunIn>({
  validationSchema: schema,
  initialValues: {
    design_agent_id: '',
    teacher_agent_id: '',
    peer_agent_id: '',
    activity_type_id: '',
    difficulty: 'introductory',
    rounds: 3,
    peer_error_pattern: 'one_in_three',
    tone: 'warm',
    notes: '',
  },
})
const [designAgentId] = defineField('design_agent_id')
const [teacherAgentId] = defineField('teacher_agent_id')
const [peerAgentId] = defineField('peer_agent_id')
const [activityTypeId] = defineField('activity_type_id')
const [difficulty] = defineField('difficulty')
const [rounds] = defineField('rounds')
const [peerErrorPattern] = defineField('peer_error_pattern')
const [tone] = defineField('tone')
const [notes] = defineField('notes')

// SSelect emits `string | number`; the form keeps the declared types.
const roundsModel = computed<number>({
  get: () => rounds.value ?? 3,
  set: (v) => {
    rounds.value = Number(v)
  },
})
// `notes` is optional in DesignRunIn, so the field reads as possibly undefined.
const notesModel = computed<string>({
  get: () => notes.value ?? '',
  set: (v) => {
    notes.value = v
  },
})

const onSubmit = handleSubmit(async (values) => {
  try {
    await design.create(values)
    startingOver.value = false
  } catch (err) {
    toast.error(
      isProblemWithType(err, '/agents/design-run-in-flight')
        ? t('dashboard.design.inFlight')
        : t('dashboard.design.submitFailed'),
    )
  }
})

async function onApply(): Promise<void> {
  const current = run.value
  if (!current) return
  const ok = await confirm({
    title: t('dashboard.design.applyTitle'),
    message: t('dashboard.design.applyMessage', {
      teacher: agentName(current.teacher.agent_id),
      peer: agentName(current.peer.agent_id),
    }),
    confirmLabel: t('dashboard.design.apply'),
    variant: 'warning',
  })
  if (!ok) return
  const outcome = await design.apply(current.id)
  if (outcome === 'applied') toast.success(t('dashboard.design.applied'))
  else if (outcome === 'not-applicable') toast.error(t('dashboard.design.notApplicable'))
  else if (outcome === 'failed') toast.error(t('dashboard.design.applyFailed'))
}

const targets = computed<{ role: 'teacher' | 'peer'; target: DesignTargetOut }[]>(() =>
  run.value
    ? [
        { role: 'teacher', target: run.value.teacher },
        { role: 'peer', target: run.value.peer },
      ]
    : [],
)
</script>

<template>
  <section aria-labelledby="dashboard-design-heading">
    <SCard class="p-4">
      <div class="mb-3 flex items-start gap-2">
        <SparklesIcon
          class="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-muted)]"
          aria-hidden="true"
        />
        <div>
          <h2
            id="dashboard-design-heading"
            class="text-lg font-semibold text-[var(--color-fg)]"
          >
            {{ t('dashboard.design.title') }}
          </h2>
          <p class="text-sm text-[var(--color-muted)]">
            {{ t('dashboard.design.description') }}
          </p>
        </div>
      </div>

      <SQueryError
        v-if="design.error.value"
        :message="design.error.value.message"
        :retry-label="t('dashboard.retry')"
        @retry="design.refetch()"
      />

      <div
        v-else-if="inFlight"
        class="flex items-center gap-2 text-sm text-[var(--color-muted)]"
        role="status"
        data-testid="design-in-flight"
      >
        <SLoadingSpinner size="sm" />
        {{ t(`dashboard.design.status.${run?.status}`) }}
      </div>

      <template v-else>
        <SAlert
          v-if="conflict"
          variant="warning"
          class="mb-3"
          data-testid="design-conflict"
        >
          {{ t('dashboard.design.conflict') }}
        </SAlert>
        <SAlert
          v-else-if="run?.status === 'failed' && !startingOver"
          variant="danger"
          class="mb-3"
          data-testid="design-failed"
        >
          {{ t(`dashboard.design.failure.${run.failure_reason ?? 'design_unavailable'}`) }}
        </SAlert>
        <SAlert
          v-else-if="run?.status === 'applied' && !startingOver"
          variant="success"
          class="mb-3"
          data-testid="design-applied"
        >
          {{ t('dashboard.design.appliedNote', {
            teacher: agentName(run.teacher.agent_id),
            peer: agentName(run.peer.agent_id),
          }) }}
        </SAlert>

        <div
          v-if="run?.status === 'succeeded' && !showForm"
          data-testid="design-proposal"
        >
          <p
            v-if="run.notes"
            class="mb-3 whitespace-pre-wrap text-sm text-[var(--color-fg)] [overflow-wrap:anywhere]"
          >
            <span class="text-[var(--color-muted)]">{{ t('dashboard.design.notes') }}</span>
            {{ run.notes }}
          </p>
          <div
            v-for="{ role, target } in targets"
            :key="role"
            class="mb-4"
          >
            <h3 class="mb-2 text-sm font-semibold text-[var(--color-fg)]">
              {{ t(`dashboard.design.target.${role}`, { name: agentName(target.agent_id) }) }}
            </h3>
            <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div>
                <SBadge
                  size="sm"
                  variant="neutral"
                >
                  {{ t('dashboard.design.current') }}
                </SBadge>
                <pre
                  class="mt-1 max-h-80 overflow-auto whitespace-pre-wrap rounded border border-[var(--color-border-subtle)] p-2 text-xs text-[var(--color-fg)] [overflow-wrap:anywhere]"
                  :data-testid="`design-current-${role}`"
                >{{ target.current_prompt || t('dashboard.design.emptyPrompt') }}</pre>
              </div>
              <div>
                <SBadge
                  size="sm"
                  variant="success"
                >
                  {{ t('dashboard.design.proposed') }}
                </SBadge>
                <pre
                  class="mt-1 max-h-80 overflow-auto whitespace-pre-wrap rounded border border-[var(--color-border-subtle)] p-2 text-xs text-[var(--color-fg)] [overflow-wrap:anywhere]"
                  :data-testid="`design-proposed-${role}`"
                >{{ target.proposed_prompt }}</pre>
              </div>
            </div>
          </div>
          <div class="flex flex-wrap justify-end gap-2">
            <SButton
              type="button"
              variant="secondary"
              data-testid="design-start-over"
              @click="startingOver = true"
            >
              {{ t('dashboard.design.startOver') }}
            </SButton>
            <SButton
              type="button"
              variant="primary"
              :loading="design.applying.value"
              data-testid="design-apply"
              @click="onApply"
            >
              {{ t('dashboard.design.apply') }}
            </SButton>
          </div>
        </div>

        <form
          v-if="showForm"
          class="grid grid-cols-1 gap-3 md:grid-cols-2"
          data-testid="design-form"
          novalidate
          @submit.prevent="onSubmit"
        >
          <SFormField
            :label="t('dashboard.design.field.designAgent')"
            name="design-design-agent"
            :error="errors.design_agent_id ?? ''"
          >
            <SSelect
              v-model="designAgentId"
              :options="agentOptions"
              :placeholder="t('dashboard.design.choose')"
            />
          </SFormField>
          <SFormField
            :label="t('dashboard.design.field.activityType')"
            name="design-activity-type"
            :error="errors.activity_type_id ?? ''"
          >
            <SSelect
              v-model="activityTypeId"
              :options="typeOptions"
              :placeholder="t('dashboard.design.choose')"
            />
          </SFormField>
          <SFormField
            :label="t('dashboard.design.field.teacherAgent')"
            name="design-teacher-agent"
            :error="errors.teacher_agent_id ?? ''"
          >
            <SSelect
              v-model="teacherAgentId"
              :options="agentOptions"
              :placeholder="t('dashboard.design.choose')"
            />
          </SFormField>
          <SFormField
            :label="t('dashboard.design.field.peerAgent')"
            name="design-peer-agent"
            :error="errors.peer_agent_id ?? ''"
          >
            <SSelect
              v-model="peerAgentId"
              :options="agentOptions"
              :placeholder="t('dashboard.design.choose')"
            />
          </SFormField>
          <SFormField
            :label="t('dashboard.design.field.difficulty')"
            name="design-difficulty"
          >
            <SSelect
              v-model="difficulty"
              :options="DIFFICULTIES.map((v) => ({ value: v, label: t(`dashboard.design.difficulty.${v}`) }))"
            />
          </SFormField>
          <SFormField
            :label="t('dashboard.design.field.rounds')"
            name="design-rounds"
          >
            <SSelect
              v-model="roundsModel"
              :options="ROUNDS.map((v) => ({ value: v, label: String(v) }))"
            />
          </SFormField>
          <SFormField
            :label="t('dashboard.design.field.peerErrorPattern')"
            name="design-peer-error-pattern"
          >
            <SSelect
              v-model="peerErrorPattern"
              :options="ERROR_PATTERNS.map((v) => ({ value: v, label: t(`dashboard.design.errorPattern.${v}`) }))"
            />
          </SFormField>
          <SFormField
            :label="t('dashboard.design.field.tone')"
            name="design-tone"
          >
            <SSelect
              v-model="tone"
              :options="TONES.map((v) => ({ value: v, label: t(`dashboard.design.tone.${v}`) }))"
            />
          </SFormField>
          <SFormField
            class="md:col-span-2"
            :label="t('dashboard.design.field.notes')"
            name="design-notes"
            :error="errors.notes ?? ''"
            :help="t('dashboard.design.notesHelp', { max: NOTES_MAX })"
          >
            <STextarea
              v-model="notesModel"
              :rows="3"
              :maxlength="NOTES_MAX"
            />
          </SFormField>
          <div class="flex justify-end gap-2 md:col-span-2">
            <SButton
              v-if="startingOver"
              type="button"
              variant="secondary"
              @click="startingOver = false"
            >
              {{ t('dashboard.design.backToProposal') }}
            </SButton>
            <SButton
              type="submit"
              variant="primary"
              :loading="design.creating.value"
              data-testid="design-submit"
            >
              {{ t('dashboard.design.submit') }}
            </SButton>
          </div>
        </form>
      </template>
    </SCard>
  </section>
</template>
