import type { ScenarioInput } from './schema.ts'

type StepInput = ScenarioInput['steps'][number]

/** Activation notices are the only room-visible round markers: the deadline and completion notices reach TA alone. */
const STARTED = 'started\\.'
const ENDED = 'ended\\.'

// An actor's steps run in order, so `at: 0` on a follow-up step means "right after the previous one".

/** A round can wait on a long sharing phase and TA's own pace, so nothing here times out early. */
const ROUND_TIMEOUT_SEC = 20 * 60

export function onRoundStart(actor: string, round: number): StepInput {
  return {
    actor,
    do: 'openActivity',
    when: { type: 'message', from: 'system', match: STARTED, sinceArmed: false, count: round },
    timeoutSec: ROUND_TIMEOUT_SEC,
    label: `opens the form in round ${round}`,
  }
}

export function onRoundEnd(actor: string, round: number): StepInput {
  return {
    actor,
    do: 'wait',
    sec: 1,
    when: { type: 'message', from: 'system', match: ENDED, sinceArmed: false, count: round },
    timeoutSec: ROUND_TIMEOUT_SEC,
  }
}

/**
 * Keeps the actor connected until TA closes the class after its last round. The run ends when every
 * actor's steps are done, and silence wakes need a connected human, so without this the room empties
 * right after the last share and TA never gets the turn that summarises and closes.
 */
export function onClassClose(actor: string): StepInput {
  return {
    actor,
    do: 'wait',
    sec: 5,
    when: { type: 'message', from: 'agent:TA', match: '用完|出完|下課|感謝' },
    timeoutSec: 10 * 60,
    label: 'sees TA close the class',
  }
}

/** One student's part in one individual round: open, think, submit (or not), share. */
export function individualRound(o: {
  actor: string
  round: number
  thinkSec: number
  fields: Record<string, string> | null
  share?: { delaySec: number; text: string }
}): StepInput[] {
  const steps: StepInput[] = [onRoundStart(o.actor, o.round), { actor: o.actor, at: 0, do: 'wait', sec: o.thinkSec }]
  if (o.fields) steps.push({ actor: o.actor, at: 0, do: 'submit', fields: o.fields, label: `submits in round ${o.round}` })
  if (o.share) {
    steps.push(
      onRoundEnd(o.actor, o.round),
      { actor: o.actor, at: 0, do: 'wait', sec: o.share.delaySec },
      { actor: o.actor, at: 0, do: 'say', text: o.share.text, label: `shares after round ${o.round}` },
    )
  }
  return steps
}
