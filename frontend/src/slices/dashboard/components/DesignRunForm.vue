<script setup lang="ts">
// The design form ([R34.08]). It validates and emits; the panel submits.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useForm } from 'vee-validate'
import { z } from 'zod'
import { SButton, SFormField, SSelect, STextarea } from '@shared/ui'
import { toTypedSchema } from '@shared/utils/zodTypedSchema'
import type { DesignRunIn } from '@shared/api-client'

interface Option {
  value: string
  label: string
}

defineProps<{
  agentOptions: Option[]
  typeOptions: Option[]
  creating: boolean
  // A proposal is on screen behind the form, so offer the way back to it.
  canGoBack: boolean
}>()
const emit = defineEmits<{
  submit: [values: DesignRunIn]
  back: []
}>()

const { t } = useI18n()

const NOTES_MAX = 2000
const DIFFICULTIES = ['introductory', 'intermediate', 'advanced'] as const
const ERROR_PATTERNS = ['none', 'one_in_three'] as const
const TONES = ['warm', 'neutral', 'concise'] as const
const ROUNDS = [1, 2, 3, 4, 5, 6] as const

const schema = computed(() =>
  toTypedSchema(
    z
      .object({
        design_agent_id: z.string().min(1, t('dashboard.design.required')),
        teacher_agent_id: z.string().min(1, t('dashboard.design.required')),
        peer_agent_id: z.string().min(1, t('dashboard.design.required')),
        activity_type_id: z.string().min(1, t('dashboard.design.required')),
        difficulty: z.enum(DIFFICULTIES),
        rounds: z
          .number()
          .int()
          .min(ROUNDS[0])
          .max(ROUNDS[ROUNDS.length - 1]!),
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

const onSubmit = handleSubmit((values) => emit('submit', values))
</script>

<template>
  <form
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
        v-if="canGoBack"
        type="button"
        variant="secondary"
        @click="emit('back')"
      >
        {{ t('dashboard.design.backToProposal') }}
      </SButton>
      <SButton
        type="submit"
        variant="primary"
        :loading="creating"
        data-testid="design-submit"
      >
        {{ t('dashboard.design.submit') }}
      </SButton>
    </div>
  </form>
</template>
