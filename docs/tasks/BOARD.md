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

- `2026-07-17-sandbox-guest-container-tests` (feature, draft) — unblocked 2026-07-20 when
  `2026-07-16-workspace-path-convention` reached `implemented`. Still `draft`, so it needs
  approval before `/build` will touch it. **Two of its ACs are now stale and must be
  rewritten first** — its own §10 anticipated exactly this ("if that dossier lands first,
  this AC must be written in its post-fix shape instead — check before building"). AC-5
  asserts `list` on a single file returns `[basename]` at `driver.py:245`; that branch is
  now `driver.py:249` and returns an absolute path. AC-4 asserts the "current flat"
  listing: recursion is indeed still flat (its FU-3 is open), but the entry *shape* is now
  absolute, so the assertion needs rewriting even though the flatness claim survives.

## Blocked

Nothing blocked.

## In progress

- `2026-07-19-session-dir-room-isolation` (bugfix) — `depends_on: []`.
- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.
- `2026-07-19-workspace-readonly-in-kernel` (bugfix) — `depends_on:
  [2026-07-19-session-dir-room-isolation]`. **Note:** that dependency is not yet
  `implemented` (it's `in-progress` too) — this dossier's fix cites code
  (`docker_runsc.py:1190-1193`) that the dependency introduced, so verify that code has
  actually landed on the working tree before resuming this one; if it hasn't, finish the
  dependency first per the `depends_on` gate in `/build`'s contract.
