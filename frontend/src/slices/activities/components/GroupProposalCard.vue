<script setup lang="ts">
// One group's live vote ([R30.41]). Renders the threshold as a SENTENCE, never
// as a `2/3` glyph: a bare fraction in an activity panel reads as a score, and
// this is the number of people who still have to agree (§7 of the dossier).

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SButton } from '@shared/ui'
import { isProposalOpen, type ActivityGroupProposal } from '../types'

const props = defineProps<{
  proposal: ActivityGroupProposal
  /** The group's display name, or null when it is not in the caller's eligible
   *  set — the code is never shown instead, since a uuid tells a student less
   *  than "your group" does. */
  groupName: string | null
  /** true approved, false rejected, null undecided. */
  myVote: boolean | null
  isProposer: boolean
  pending: boolean
  /** No identity resolved yet: show the state, disable the controls. */
  canVote: boolean
}>()

const emit = defineEmits<{
  vote: [approve: boolean]
  withdraw: []
}>()

const { t } = useI18n()

const isOpen = computed(() => isProposalOpen(props.proposal.status))
const hasVoted = computed(() => props.myVote !== null)

const title = computed(() =>
  props.groupName
    ? t('activities.group.cardTitle', { group: props.groupName })
    : t('activities.group.cardTitleUnnamed'),
)

// "Needs N of M to agree. K so far." — the count still outstanding is the thing
// a participant acts on, so it leads the second sentence.
const thresholdText = computed(() =>
  t('activities.group.threshold', {
    required: props.proposal.required_approvals,
    total: props.proposal.voter_count,
    approvals: props.proposal.approvals,
  }),
)

const outstandingText = computed(() =>
  t('activities.group.stillDeciding', { count: props.proposal.undecided }),
)

const statusText = computed(() => {
  switch (props.proposal.status) {
    case 'accepted':
      return t('activities.group.statusAccepted')
    case 'rejected':
      return t('activities.group.statusRejected')
    case 'withdrawn':
      return t('activities.group.statusWithdrawn')
    case 'expired':
      return t('activities.group.statusExpired')
    default:
      return t('activities.group.statusOpen')
  }
})

const myVoteText = computed(() =>
  props.myVote === true ? t('activities.group.myVoteApproved') : t('activities.group.myVoteRejected'),
)

// The payload as label/value rows. Object-shaped by construction (the type's
// schema is an object), and rendered as text — never as markup, because it is
// participant-written and this card carries no sanitizer.
const answerRows = computed(() =>
  Object.entries(props.proposal.payload ?? {})
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([field, value]) => ({
      field,
      text: typeof value === 'object' ? JSON.stringify(value) : String(value),
    })),
)
</script>

<template>
  <section
    class="proposal-card"
    data-testid="group-proposal-card"
  >
    <p class="proposal-card__title">
      {{ title }}
    </p>
    <p
      class="proposal-card__status"
      data-testid="group-proposal-status"
    >
      {{ statusText }}
    </p>

    <template v-if="isOpen">
      <p
        class="proposal-card__threshold"
        data-testid="group-proposal-threshold"
      >
        {{ thresholdText }}
      </p>
      <p class="proposal-card__outstanding">
        {{ outstandingText }}
      </p>
    </template>

    <dl
      v-if="answerRows.length"
      class="proposal-card__answer"
    >
      <template
        v-for="row in answerRows"
        :key="row.field"
      >
        <dt>{{ row.field }}</dt>
        <dd>{{ row.text }}</dd>
      </template>
    </dl>

    <p
      v-if="hasVoted"
      class="proposal-card__my-vote"
      data-testid="group-proposal-my-vote"
    >
      {{ myVoteText }}
    </p>

    <div
      v-if="isOpen"
      class="proposal-card__actions"
    >
      <!-- A vote is final: the server records one row per pinned voter and
           refuses a second, so re-showing the buttons afterwards would only
           offer a click that 409s. -->
      <template v-if="!hasVoted">
        <SButton
          variant="primary"
          :loading="pending"
          :disabled="!canVote || pending"
          data-testid="group-proposal-approve"
          @click="emit('vote', true)"
        >
          {{ t('activities.group.approve') }}
        </SButton>
        <SButton
          variant="secondary"
          :loading="pending"
          :disabled="!canVote || pending"
          data-testid="group-proposal-reject"
          @click="emit('vote', false)"
        >
          {{ t('activities.group.reject') }}
        </SButton>
      </template>
      <SButton
        v-if="isProposer"
        variant="danger"
        :loading="pending"
        :disabled="pending"
        data-testid="group-proposal-withdraw"
        @click="emit('withdraw')"
      >
        {{ t('activities.group.withdraw') }}
      </SButton>
    </div>
  </section>
</template>

<style scoped>
.proposal-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.proposal-card__title {
  margin: 0;
  font-weight: var(--weight-semibold);
}
.proposal-card__status,
.proposal-card__outstanding,
.proposal-card__my-vote {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--color-muted);
}
.proposal-card__threshold {
  margin: 0;
  font-size: var(--font-size-sm);
}
.proposal-card__answer {
  display: grid;
  /* The label column sizes to its content but never crowds the answer out at
     375px, which is where the rail lives on a phone. */
  grid-template-columns: minmax(0, auto) minmax(0, 1fr);
  gap: var(--space-1) var(--space-2);
  margin: 0;
  font-size: var(--font-size-sm);
}
.proposal-card__answer dt {
  color: var(--color-muted);
  overflow-wrap: anywhere;
}
.proposal-card__answer dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.proposal-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
</style>
