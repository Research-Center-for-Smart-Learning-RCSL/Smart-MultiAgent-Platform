# Task Board

Derived view over every dossier under `docs/tasks/` that is not
`implemented`/`superseded`/`abandoned`, grouped by `depends_on` + `status` per the rules
in `README.md`. If this file and a dossier's own frontmatter disagree, the frontmatter
wins — this is a cache, not a second source of truth. Maintained by `/spec` (adds a row
on dossier creation) and `/build` (moves a row on every status change).

Backfilled 2026-07-20 for the dossiers active at that date; the other ~80 dossiers under
`docs/tasks/` were already `implemented`/`superseded` and are intentionally not listed
here (see README.md's Dependencies and sequencing section for why untouched history
doesn't need a `depends_on` backfill).

## Ready now

Nothing blocking; these can start in any order relative to each other, including in
parallel.

- `2026-07-07-graphrag-two-axis-redesign` (feature, approved) — `depends_on: []`. This is
  a blueprint dossier: approval authorizes the target design, and its phases are meant to
  become separate `/build` dossiers (see its own §1). Open question: `docs/tasks/2026-07-07-graphrag-phase0..4b-*`
  already exist and are all `status: implemented` with overlapping `[Rxx.yy]` coverage —
  worth confirming with the user whether this blueprint's remaining scope is still live or
  its status is simply stale, before treating it as unblocked work.

## Blocked

Nothing blocked.

## In progress

- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.

- `2026-07-17-sandbox-guest-container-tests` (feature) - **unblocked 2026-07-20** when both
  07-19 dependencies reached `implemented`; started 2026-07-21. Approved after a revision
  that closed OQ-1/OQ-2 and rewrote three ACs: the two 07-19 dossiers had falsified its §4
  model (the kernel moved to `/session` on its own per-room volume and `/workspace` became
  read-only there), so its AC-7 was inverted to assert [R12.03b]'s mount isolation instead
  of the old "three roots share one volume". Carries a new AC-13 added after
  `session-dir-room-isolation` D-10 shipped a defect that broke `code_exec` in every
  chatroom and that only a real container could catch - which is this dossier's whole
  premise, now with a concrete incident behind it.
