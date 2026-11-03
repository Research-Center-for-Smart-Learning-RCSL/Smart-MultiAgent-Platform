import { defineScenario } from '../scenario/schema.ts'
import base from './crat-autonomous.ts'

// Phase 5 AC-10 and phase 6 AC-4: docs/tasks/2026-11-03-crat-facilitation-phase5-class-close and
// docs/tasks/2026-11-03-crat-facilitation-phase6-reply-commentary-leak. The crat-autonomous class,
// then P1 stays connected for sixteen minutes after TA closes the class, long enough for several
// silence wakes (TA's t_minutes is 2) that a closed room must drop. The simulator cannot send a
// mention, so the operator @mentions TA in the room during the hold; the run ends when the hold does.

export default defineScenario({
  ...base,
  name: 'crat-class-close',
  description: 'Phase 5 AC-10 / phase 6 AC-4: the CRAT class, then sixteen silent minutes after TA closes it.',
  durationSec: 75 * 60,
  room: { ...base.room, name: 'sim-p5-crat-{ts}' },
  steps: [...base.steps, { actor: 'P1', at: 0, do: 'silent', sec: 16 * 60, label: 'stays connected after the close' }],
  notes: [
    ...(base.notes ?? []),
    'Close (phase 5 AC-10): TA calls close_class in the turn that ends the activity; the class-start card shows the class closed; no agent message follows the close other than the answer to the operator mention.',
    'Mention: during the hold the operator @mentions TA in the room and TA answers briefly.',
    'Final answer only (phase 6 AC-4): no agent message contains planning text; agent.turn_finished rows of TA turns carry output_shape.',
  ],
})
