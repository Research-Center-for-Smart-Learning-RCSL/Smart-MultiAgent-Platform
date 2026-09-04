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

### From the 2026-08-30 FU consolidation

Three dossiers opened the same day from the follow-up lists of four already-implemented ones.
All three are `depends_on: []` and each verified its freshness against `main` at `73125821`.
They were grouped so that each is independently reviewable: the clipboard sweep that would have
put two of them in `ChatroomView` at once was returned to its source follow-up instead. They
opened `draft` and needed explicit approval before `/build` would touch them; two carry draft SRS
deltas that are deliberately not applied at consolidation time. `identity-onboarding-policy-hardening`
was approved and implemented on 2026-08-31; `runtime-contract-integrity` on 2026-09-01. Both are
out of Ready; their close-out notes are under In progress below.

- (implemented 2026-08-31; see the note under In progress.)
  The original entry, kept here for its detail:
  `2026-08-30-identity-onboarding-policy-hardening` (feature, **draft**) - `depends_on: []`.
  Consolidates FU-6/FU-9/FU-10/FU-11 of `2026-08-20-onboarding-without-smtp`: make the
  email-domain policy durable and admin-manageable, refuse banned-account activation links,
  and rate-limit per-admin account provisioning. Its compatibility/active/rollback-frozen
  rollout states, atomic legacy import and write fence, and versioned Redis TTL contract close
  the migration hazards found during review. **Read its Q-8a before implementing the reader**:
  the rollout state is resolved from the same cached value as the policy, because a reader that
  re-read the phase per request would defeat the cache and one that cached the phase alone would
  enforce a stale authority. Carries a draft [R19a.13] amendment.

- (implemented 2026-09-01; see the note under In progress.) The original entry, kept here for its
  detail: `2026-08-30-runtime-contract-integrity` (bugfix, **draft**) - `depends_on: []`.
  Consolidates FU-5 of `2026-08-20-orchestration-room-scoped-reads` with FU-4 of
  `2026-08-27-provider-model-capability-table`: repair two dead typed-error recovery branches and
  make provider seed support an independent backend/UI capability, forwarding Gemini's supported
  seed while keeping unsupported OpenAI Responses and Anthropic controls inert. **The generated
  `ApiError` those two branches test for is unreachable, not merely rare** — the same rejection
  handler is registered on the bare `axios` singleton the generated services use, so
  `parseProblem` converts every failure first and no test could ever go red. Two same-file,
  disjoint-region overlaps are recorded in its Q-2 (`AgentDetailView.vue` with the graphrag
  blueprint, `turn_engine.py` with the large-artifacts dossier) and are deliberately **not**
  `depends_on`, so whoever builds second rebases.

### From the 2026-08-27 provider-model investigation

Both dossiers from this investigation are `implemented` as of 2026-08-28 and neither is active work.
`2026-08-27-provider-model-capability-table` unblocked its sibling and both were removed from In
progress the same day; their close-out notes are under In progress below. This section is kept as
the record of what the investigation produced. Its remaining live thread is
`2026-08-30-runtime-contract-integrity` in Ready above, which carries the capability table's FU-4.

- (implemented 2026-08-28; see the note under In progress. Nothing lists it in `depends_on`, so no
  row moves out of Blocked.) The original entry, kept here for the record:
  `2026-08-27-openai-responses-api-migration` (feature, **approved 2026-08-27**; its SRS Delta is
  None). **Logical prerequisite** (its Q-2): the migration's value is model-specific, and the
  capability table is where model facts are expressed; building this first would encode them a
  second time, which is the condition the table exists to end. The two also share `openai.py`'s
  request builder.

  **Read the capability table's own close-out before starting.** It shipped with AC-6, AC-11 and
  AC-15 deliberately deferred (its Deviation Log D-1/D-2, FU-11/FU-12/FU-13): the model lineup and
  per-model capability values were re-expressed from the pre-existing dictionaries/regexes rather
  than reconciled against a live provider key, and §4.3a's omission-vs-`"none"` question for
  gpt-5.4+ was not verified against the real endpoint either. This dossier's own §10 already plans
  to need a real key against the real endpoint, so folding FU-12's verification into that same
  live-endpoint pass (rather than running it twice) is worth considering at that dossier's start,
  not assumed settled by the table shipping.

  **It is deliberately assess-first and its §6 is empty on purpose.** The Responses API changes
  request shape, response shape and the SSE event vocabulary at once, on the adapter serving the
  platform's default provider. §5 lists six questions that must be answered with citations, and
  AC-2 is the user's approval of the recommendation. **"Do not migrate" is a listed option**; if it
  wins, the dossier closes `abandoned` and the assessment is its product.

  **The reason it exists**: on Chat Completions the platform can offer either reasoning effort or
  agent tools, never both, from gpt-5.4 onwards. §10 records the constraint that shapes its
  verification — `fake_provider.py` cannot produce an agent turn, so this change needs a real key
  against the real endpoint, planned rather than discovered at the end.

### From the 2026-08-16 example-subsystem audit

Thirteen dossiers from `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`
(18 findings, grouped by blast radius so concurrent builds cannot produce conflicting diffs).
Every one is `depends_on: []`; the five majors are listed first. Three file-overlap pairs are
noted below — these are **not** sequenced, but whoever builds second must rebase rather than
assume. All thirteen are now implemented and removed (see the notes below the In progress
list). This section is kept as the record of what that audit produced.

### From the 2026-08-19 page-presentation audit

Five dossiers from `docs/audits/2026-08-19-page-presentation-scroll-and-feedback/findings.md`
(52 findings, all triaged, grouped by blast radius). Two of them are ready now; the other
three are in Blocked below.

The chain `transient-feedback-channels` -> `shared-overlay-and-shell-defects` ->
`content-area-spacing-and-scroll-contract` -> `mobile-viewport-and-breakpoints` is an
**overlap** chain, not a logical one: they share `App.vue`, `AppShell.vue`, `router.ts` and
`AgentDetailView.vue`, and concurrent builds would conflict. Any of them could technically go
first, but building them serially avoids the conflict.

- (implemented 2026-08-21; see the note under In progress)
  `2026-08-19-chatroom-scroll-and-composer`. The original entry, kept here for the record:
  `2026-08-19-chatroom-scroll-and-composer` (bugfix, **approved 2026-08-21**) - `depends_on: []`.
  Independent of the chain; its Q-11 records the file-by-file overlap check that justifies the
  empty list, so it can run in parallel with all four others. Its **spec delta was applied at
  approval**: `07-conversation.md:513` claimed a cache prevents per-token markdown re-rendering,
  which the audit disproved (it keys on text equality, so it cannot hit while text grows), and
  `:897`'s page size disagreed with the code (50 vs 100). No `REQUIREMENTS.md` change.
  **One scope change was made at approval**: F-29's second arm - moving the agent rail out of
  768-1023 - was **cut** and deferred to the dossier's FU-6 (Q-8 rewritten). It is the only item
  in the dossier whose correction *removes* a surface users have today rather than restoring
  one, so it deserves its own reviewable change. The two bands are disjoint by construction
  (`useBreakpoint.ts:52-53`), and T-9 now asserts at 800px that the rail is **still there**, so
  the deferral is pinned rather than merely intended.
- (implemented 2026-08-21; see the note under In progress)
  `2026-08-19-shared-overlay-and-shell-defects`. The original entry, kept here for the record:
  `2026-08-19-shared-overlay-and-shell-defects` (bugfix, draft) - unblocked by the completed
  `2026-08-19-transient-feedback-channels`. Fixes the shared overlay primitives: `STable`'s
  sticky header is inert, `SDropdown` has no flip or height cap (and measures before the menu
  exists), `ErrorBoundary` wraps the whole layout so a render error blanks the shell, and the
  404 route has no `meta`. Its impersonation-banner z-index decision must retain the completed
  predecessor's toaster contract at `--z-toast: 500`.

- (moved to In progress on 2026-08-21, approved the same day)
  `2026-08-19-content-area-spacing-and-scroll-contract`. Its Q-14 warning was already resolved
  in the dossier itself at §1.2 (the shell is `vh` and stays `vh` until
  `mobile-viewport-and-breakpoints` runs, so Q-14 now specifies `vh` and FU-8 pairs the two
  edits); §1.3 records the corrections made at approval.

- (implemented 2026-08-22; see the note under In progress)
  `2026-08-21-visual-refinement-phase1-token-adoption`. The original entry, kept for the record:
  `2026-08-21-visual-refinement-phase1-token-adoption` (refactor, **approved 2026-08-21**) -
  **unblocked 2026-08-21** by `2026-08-19-shared-overlay-and-shell-defects`, which has now
  finished editing the scoped style blocks of `STable`, `SDropdown`, `SAlert`, `SEmptyState`,
  `SModal` and `STooltip`. Rebase onto those edits rather than assuming the rules are as its
  Q-6 found them: `SModal` gained `overflow-y`/`padding`/`align-items` plus `margin: auto` on
  the panel, `SDropdown` gained `position`/`overflow-y`, `STable` gained a
  `.s-table-wrap--sticky` modifier, `SEmptyState` gained `justify-content`, and `STooltip`'s
  `z-index` moved onto `var(--z-tooltip)`. Its **AC-1 bar is zero rendered difference**, so its
  computed-style baseline must be captured *after* this rebase, not before. The full entry is
  kept below in Blocked for its other detail.

- (implemented 2026-08-21; see the note under In progress)
  `2026-08-19-mobile-viewport-and-breakpoints`. The original entry, kept here for the record:
  `2026-08-19-mobile-viewport-and-breakpoints` (bugfix, **draft**) - **unblocked 2026-08-21** by
  `2026-08-19-content-area-spacing-and-scroll-contract`. Still `draft`, so it needs approval
  before `/build` will touch it. **Read its §7 item 1 first**: the `100vh` line it targets was
  relocated by `shared-overlay-and-shell-defects` and now lives at `App.vue:79`
  (`.app-root { min-height: 100vh }`), not on `.app-shell`. And it **owns FU-8 of the dossier
  that just unblocked it**: when it moves the shell to `100dvh` it must move
  `AgentDetailView.vue`'s `lg:h-[calc(100vh-3.5rem-3rem)]` in the same change, and update that
  dossier's T-9 expected class with it. Leaving one behind reintroduces a smaller F-51 on mobile.
  Its **§1.2 records a second freshness pass run at build start**: all six findings still
  reproduce, but the sibling dossier moved enough that six claims needed correcting - notably
  `GraphragGraphView.vue` no longer holds a viewport unit at all (FU-6 closed), the paired line
  is `AgentDetailView.vue:988` not `:964`, and F-18's fix shrank to a class change because the
  bar is already a flow child of the now-unpadded view root.

### From the 2026-08-22 close of visual-refinement phase 2

- (implemented 2026-08-22; see the note under In progress)
  `2026-08-22-visual-refinement-phase3-verification-and-debt`. The original entry, kept
  here for its detail:
  `2026-08-22-visual-refinement-phase3-verification-and-debt` (refactor, **draft** — needs
  approval before `/build` will touch it) — `depends_on: []`. Phase 2 is `implemented` and
  every predecessor is closed, so nothing sequences against this.

  **Its AC-1 is the reason CI is currently red.** The parity baseline
  (`e2e/baselines/visual-token-parity.json`) predates phase 2's restyle, so
  `00-visual-token-parity` reports 21 failures — correctly, on a dossier whose whole purpose
  was to move those values. Regenerating it has **three preconditions and none is optional**
  (its §6.1): a pristine `smap_test`, a verified full `E2E_*` seed set (`global-setup.ts`
  fails *silently* on an already-seeded stack and a short seed file makes every gated spec
  skip — a green run with no coverage), and then the `UPDATE_VISUAL_BASELINE=1` capture.
  `compose.test.yml` binds `28000:8000` and reuses the base project's container names, so
  **this displaces a running dev stack**; that is stated in Q-2 rather than discovered.

  **Its AC-3 to AC-7 are the visual pass phase 2 never did** — 20 C-0 surfaces, two
  viewports, two locales, two themes, recorded per surface rather than as a tick. Its Q-3
  records why this is not automated: a screenshot diff answers "did anything change", and
  phase 2 changed everything by design. Phase 2 already automated everything with a
  threshold; what is left is what only a person can answer.

  The rest is phase 2's FU-8 to FU-12: the CSP path that closes against Vite rather than
  nginx, a duplicated predicate, a px-versus-rem decision that is **per token** (a control
  height is a floor, a sidebar width is a track), an inert half of the press language, and
  an intermittent CodeMirror test.

### From the 2026-08-21 post-close code review

- (implemented 2026-08-22; see the note under In progress)
  `2026-08-22-safe-area-uncovered-top-surfaces`. The original entry, kept here for the record:
  `2026-08-22-safe-area-uncovered-top-surfaces` (bugfix, **draft** — needs approval before
  `/build` will touch it) — `depends_on: []`. Opened from
  `2026-08-21-visual-refinement-phase1-token-adoption`'s FU-12. `viewport-fit=cover` removes
  the browser's own inset from **every** surface at once, so a surface the enumeration missed
  is worse off than before that change shipped — and three were missed: the impersonation
  banner (the y=0 element whenever an admin is impersonating), toasts (configured from a
  `.ts` file, so no `.vue` or `.css` mentions their geometry), and the top bar, which
  reserves a top inset it is not owed once the banner displaces it. **The root cause is not
  the three instances**: `INSET_SURFACES` was derived by reading the layout tree, and all
  three misses are surfaces that are not in it. Its Q-6 records the file overlap with
  `2026-08-21-visual-refinement-phase2-identity-and-depth` in `main.css` and why it is
  deliberately not a `depends_on` — disjoint regions, and Q-4 keeps the toaster fix out of
  that file entirely. **AC-6 is a device check and will most likely close unticked**: nothing
  in CI emulates a display cutout.

### From the 2026-08-24 SRS update for the activity-context work

- (implemented 2026-08-24; see the note under In progress)
  `2026-08-24-traceability-extraction-gate`. **It unblocked
  `2026-08-24-observer-presentation-blocks`**, moved to Ready below — that dossier's only
  dependency was this one. `agent-readable-live-drafts` and `group-activity-submissions`
  each lose one of their three and stay Blocked on `observer-presentation-blocks`.
  The original entry, kept here for the record:
  `2026-08-24-traceability-extraction-gate` (feature, **approved 2026-08-24**) —
  `depends_on: []`. `docs/traceability.csv` is the index from every `[Rxx.yy]` to the SRS
  section defining it, and **83 of 389 requirements have no row** (R30 38, R11 19, R13 10,
  R6 5, R15 4, R12 2, R24 2, R5/R9/R14 1 each). §27 has always instructed "re-run the
  extraction", but **no extraction tool was ever built** — the file came from a one-off
  author pass on 2026-04-25 and has been hand-maintained per chapter since, which is why §31
  is complete (its dossier added the rows explicitly) and §30 has none of its 38. Builds the
  script, wires `--check` into `repo-gates`, regenerates the file, and rewrites §27. Its
  **SRS Delta was applied at approval**: §27's first paragraph rewritten and `[R27.01]`
  added — the first requirement §27 has ever defined.

  **Two things a builder needs.** Its Q-2 decides the `summary` column is *derived*, so the
  backfill commit rewrites all 306 existing rows rather than appending 83; AC-6 makes
  reviewing that diff a criterion, and the rule is to fix a bad summary by editing the SRS
  sentence, never by special-casing the script. And **the citation half of the gate already
  has a target**: `docs/implement/E-agents-knowledge.md:62,72` cites `[R9.04]`, `[R9.05]` and
  `[R9.08]`, all removed by §31 on 2026-07-16, so AC-7 requires the check to go red on the
  repo as it stands before AC-8 fixes them.

  **Read its §4 before writing the parser.** Three ID shapes (`Rn.nn`, `Rn.nn`+letter,
  `Rn.nn.nn`) and three definition forms (bullet, bare paragraph, numbered item) are all in
  use. The first pass of the analysis that produced this dossier used `\[R(\d+\.\d+)\]` and
  reported nine non-existent "stale" rows — the 16 IDs that pattern cannot see are 4 % of the
  corpus, and getting this wrong looks like a finding rather than a bug.

### Other ready work

- (moved to In progress on 2026-08-20) `2026-08-20-member-groups-and-room-visibility-isolation`.
  The original entry, kept here for the record:
  Two staged deliverables in one dossier. **Stage 1 is a confidentiality fix and can ship on its
  own**: `list_chatrooms` (`chatrooms.py:242-269`) is the one read surface that does not go
  through `_satisfies_room_flags`, so any Org Member can enumerate the name, access flags and
  observer status of every room in every project of that Org, and `list_visible_for_user`
  (`project_service.py:111-122`) lists every project of every org the caller belongs to
  regardless of project membership. Stage 2 adds an optional per-project Member Group layer
  plus a fifth room flag `allow_member_groups`, evaluated as a tier inside
  `_satisfies_room_flags` rather than as a seventh Role. It **applied an SRS Delta** at
  approval, adding [R5.06], rewriting §13.2 and [R13.04] (five flags, plus a server-refused
  mutual exclusion), and adding §13.2a [R13.28]-[R13.32]. Migration 0079. **Two things a
  builder needs before starting.** Its Q-7 records a file overlap with the still-draft
  `2026-08-19-content-area-spacing-and-scroll-contract`, which rewrites
  `ChatroomSettingsView.vue:227`'s template root while this dossier edits the access-flag
  block at `:320-395`: deliberately **not** a `depends_on`, so whoever builds second rebases.
  And its §12 carries a verification constraint rather than a preference: the last seven
  dossiers in this area closed with no `db`/`integration` run and no browser pass, and this
  one's central claim is a confidentiality claim, so AC-1, AC-4, AC-9 and AC-12 must be
  executed against a real stack or left unticked.

- (implemented 2026-08-21; see the note under In progress) `2026-08-20-onboarding-without-smtp`.
  The original entry, kept here for the record:
  `2026-08-20-onboarding-without-smtp` (feature, approved) - `depends_on: []`. Opened from the
  member-groups dossier's FU-1. **Read its §1 correction before scoping anything here**: SMTP is
  *not* required for an invitee who already has an account — `_notify_invitee`
  (`invite_service.py:159-183`) writes an in-app notification and the invite is listed at
  `/invites` by case-insensitive email match, so the real gaps are only the unregistered invitee
  and the missing admin-provisioning route. Three additive pieces: every invite create returns a
  copyable accept link, the project invite form picks from the parent Org's member list (Q-6: a
  pool the inviter can already read, so zero new disclosure), and `POST /api/admin/users` creates
  an unverified account returning two copyable activation links. It **applied an SRS Delta** at
  approval, rewriting [R6.09] and adding [R6.18]. Two decisions worth not undoing: **consent is
  preserved at both levels** (Q-1 - nothing writes a membership row outside
  `_finalize_acceptance`, and AC-10 pins that), and **the invite response never reveals whether
  the address has an account** (Q-5 - it would be the oracle `register` deliberately avoids at
  `auth_service.py:167-195`). File overlap with the member-groups dossier in
  `ProjectMembersView.vue`, `projects.py` and the tenancy locales, all different regions;
  deliberately not a `depends_on`, so whoever builds second rebases.

- (implemented 2026-08-21; see the note under In progress)
  `2026-08-20-orchestration-room-scoped-reads`. The original entry, kept here for the record:
  `2026-08-20-orchestration-room-scoped-reads` (feature, approved) - `depends_on: []`. Opened
  from the member-groups dossier's FU-2. **It found a live SRS contradiction**: [R14.10]
  (`REQUIREMENTS.md:778`) says the workflow trace is visible to Admin + Project Owners, and
  `workflows.py` grants it to any project member at `:332`, `:354`, `:546`, `:569`, `:602`. And a
  hole that exists today with no relation to grouping: an `allow_project_owners_only` room's
  approvals are readable by any project member, because `_assert_project_member`
  (`orchestration.py:52-65`) never consults the room. Fix is a dual track - a record carrying a
  `chatroom_id` goes through `resolve_room_access`/`ensure_can_read`, a record carrying none
  follows [R14.10]. Verified landing points: only `approvals` (`tables.py:79`) and
  `agent_instances` (`:167`) have the column; `instructions`, `workflow_runs` and
  `workflow_steps` have none. It **applied an SRS Delta** at approval, strengthening [R14.10] and
  adding [R15.24]. **The DLQ route is deliberately untouched** (FU-1): `DlqViewer` renders inside
  `ChatroomSettingsView.vue:547` and that view's real audience was not established, so tightening
  it could remove a panel from ordinary members. AC-8 is the regression this change is most
  likely to cause and no unit test can see it.

- `2026-07-07-graphrag-two-axis-redesign` (feature, approved) — `depends_on: []`. This is
  a blueprint dossier: approval authorizes the target design, and its phases are meant to
  become separate `/build` dossiers (see its own §1). Open question: `docs/tasks/2026-07-07-graphrag-phase0..4b-*`
  already exist and are all `status: implemented` with overlapping `[Rxx.yy]` coverage —
  worth confirming with the user whether this blueprint's remaining scope is still live or
  its status is simply stale, before treating it as unblocked work.

- (moved to In progress on 2026-09-04) `2026-09-04-guest-anonymous-session`. The original
  entry, kept here for the record:
  `2026-09-04-guest-anonymous-session` (feature, **approved 2026-09-04**) — `depends_on: []`. Replace
  the current guest-link flow (full registration + email verification, 7+ screen
  transitions) with a lightweight anonymous session: click link, enter display name,
  enter chatroom. New `guest_sessions` table, chatroom-scoped guest JWT, extended
  `Principal`, `"guest"` sender type. Three implementation phases (backend core, frontend
  direct entry, UX polish), each intended for a separate session. Amends R5.04, R6.02,
  R6.11, R13.06; adds R13.06a, R13.06b.

## Blocked

From the 2026-08-19 page-presentation audit. Every entry below is blocked only by file
overlap, so each unblocks as soon as its predecessor is `implemented`.

### From the 2026-08-21 visual-refinement analysis

Two dossiers, sequenced. Not from an audit: they came from a direct read of `main.css`, the
46 `shared/ui/` components and the two `docs/UI/` specification files, prompted by the user's
report that the UI is consistent but flat.

**Both are `implemented` as of 2026-08-22** and their rows below are kept only as the record
of what they were waiting on. Phase 2 closed with two criteria unticked; they belong to
`2026-08-22-visual-refinement-phase3-verification-and-debt` in Ready above, which is also
the row to read for why CI is red.

- (moved to Ready now on 2026-08-21, unblocked by the implemented
  `2026-08-19-shared-overlay-and-shell-defects`) The original entry, kept here for its detail:
  `2026-08-21-visual-refinement-phase1-token-adoption` (refactor, **approved 2026-08-21**) - waiting on
  `2026-08-19-shared-overlay-and-shell-defects`. **Overlap prerequisite only** (its Q-6): that
  dossier edits the scoped style blocks of `STable`, `SDropdown`, `SAlert`, `SEmptyState`,
  `SModal` and `STooltip`, and this one edits the same rules. Makes the design tokens
  load-bearing: three of 46 shared components consume a `--font-size-*`/`--space-*`/
  `--weight-*`/`--elevation-*` token today, against 109 raw type and 168 raw spacing
  declarations. **Zero rendered difference is the acceptance bar** (AC-1), pinned by a
  computed-style baseline captured before any edit. Two things a builder needs first: its Q-8
  requires `docs/UI/00-overview.md` and `docs/UI/01-design-system.md` to be rewritten in the
  same series, because those documents specify component sizing in literal pixels
  (`01-design-system.md:126-132,249,400`) and are the reason each new component is written
  that way; and its Q-5 keeps the sweep out of view *template roots*, which belong to
  `content-area-spacing-and-scroll-contract`'s F-3/F-40.
- (moved to In progress on 2026-08-22. Its freshness pass found four material drifts and
  confirmed phase 1's hand-off: **FU-5, FU-6, FU-7 and FU-9 were all taken into scope with
  the user** rather than deferred again, so this build also declares the five orphan colour
  properties, lifts `@layer base`'s heading ramp onto the documented page-level roles, moves
  `--space-*` from px to rem, and resolves the four zero-consumer tokens. FU-8 stays a
  coverage note. The drifts: `--focus-ring` has **29 references in 24 component files**, not
  the six §6.6 enumerates; the parity spec is `00-`, not `20-`, and `21-` is taken so the new
  spec is `24-`; §4.5's "no `font-variant-numeric` anywhere" is false at two non-table sites;
  and §12's source sweep is in `shared/styles/__tests__/`, not `app/__tests__/`.)
  The original entry, kept here for its detail:
  `2026-08-21-visual-refinement-phase2-identity-and-depth` (feature, **approved 2026-08-21**) -
  waiting on phase 1, **logically**: it is almost entirely token-value edits, which reach nothing until
  phase 1 makes the components read tokens. Changes the visual identity itself, which
  `2026-07-05-sitewide-ui-enhancement` ruled out at its §2 and which is why the product looks
  as it does. Self-hosts Inter (Q-3: `smap.conf:177` sets `font-src 'self' data:`, so a CDN is
  blocked by the deployed CSP, not merely undesirable), moves every neutral from Tailwind
  `gray` onto `slate` to match the surfaces that are already slate, adds a `--color-canvas`
  role so a card stops being the same colour as the page behind it (Q-6 - no shadow value can
  fix a same-colour relationship), splits `--color-border` into boundary and interior weights,
  loosens `STable` density, and adds the pressed state that `:active` being absent from all of
  `frontend/src` means no element has. It carries an **SRS Delta**: amends [R24.28] and
  [R24.49] and adds [R24.50], **applied to `REQUIREMENTS.md` at approval on 2026-08-21**. Its
  AC-9 introduces a real contrast test rather than a manual measurement, which is what keeps
  the palette honest after it lands.

- (moved to Ready now on 2026-08-21, unblocked by the implemented
  `2026-08-19-shared-overlay-and-shell-defects`; the Q-14 warning moved with it)
- (moved to Ready now on 2026-08-21, unblocked by the implemented
  `2026-08-19-content-area-spacing-and-scroll-contract`) `2026-08-19-mobile-viewport-and-breakpoints`.

### From the 2026-08-24 observer-presentation work

Two dossiers, both **approved 2026-08-24** with their SRS Deltas applied at approval. Both were
blocked on `2026-08-24-traceability-extraction-gate`, which is **implemented as of
2026-08-24**. The first is therefore **Ready now**; the second still waits on the first. Read
each row for its own list — the frontmatter wins over this preamble.

- (implemented 2026-08-24; see the note under In progress) The original entry, kept here for
  its detail.
  **One thing changed under it while it waited**: the gate regenerated all 306 existing rows
  *and* backfilled 115 rather than 83, so `docs/traceability.csv` is now generated — its five
  new `[R28.15]`-`[R28.19]` rows are produced by `python scripts/traceability.py`, never
  hand-added, and CI rejects the commit if that step is skipped.
  `2026-08-24-observer-presentation-blocks` (feature, **approved 2026-08-24**) — waiting on
  `2026-08-24-traceability-extraction-gate`. **Overlap prerequisite only**: its SRS Delta adds
  `[R28.15]`-`[R28.19]`, each needing a `docs/traceability.csv` row, and the gate dossier
  regenerates all 306 existing rows from a script it builds. Gives an observer agent a closed
  set of platform-defined presentation blocks, delivered by one structured tool call
  (`present_observation`) on observer turns only. **The design's load-bearing split**: `prose`,
  `key_points` and `timeline` are agent-authored text, while `field_coverage`, `mandala_grid`
  and `attempt_table` are **server-computed** — the agent picks and frames them, and never
  supplies a number. That split exists because of the finding in its §4.5: no server fact says
  which worksheet fields were filled (`filled_count` records a count, not a field list, at
  `app/plugins/activity_validators.py:109-116`, and `RecentActivityRow` carries no
  `sub_scores` at all), so the only alternative was a chart drawn from the agent eyeballing a
  480-character truncation of the participant's own words. Its Q-4 supplies that fact from a
  **new** validator the example course opts into rather than by changing `filled_count`, at
  the user's explicit direction — which also stops the raw-payload dump reaching the agent
  digest for those types. **Read its §10 before touching the example**: the `validator_id`
  change does not reach an existing install, and the documented upgrade deletes the types and
  revokes every project's opt-in. Migration 0080.

  **Its §5.5 is the correction a code review caught and is the difference between the feature
  working and silently doing nothing.** `run_turn` guards on `if not final_text.strip():`
  (`turn_engine.py:2958`) and returns `skipped` *before* the observer branch at `:3026`, so a
  model that delivers its analysis as blocks and says nothing in prose — the ordinary case for
  this feature, not an edge case — would have every block discarded. The guard becomes "empty
  only when there is neither text nor blocks", and AC-16/AC-17 pin all three combinations.
  Its §6 also corrects `_CONTENT_NOTE`, which would otherwise vouch for a server-computed
  digest as the participant's own words once the example moves to `filled_count_coverage`.

- (implemented 2026-08-25; see the note under In progress. Nothing lists it in
  `depends_on` — the two dossiers that mention it do so in historical Q rows and are
  themselves already implemented — so no row moves out of Blocked.)
  The original entry, kept here for its detail:
  (moved to In progress on 2026-08-25. Its freshness pass found four drifts and one new
  surface, and two of them changed the build. **`chatroom.agents_changed` does not exist**:
  §5.1 hangs mid-session grant re-resolution on an event the settings write is said to
  already publish, and `set_agent_activity_grant` publishes nothing — `chatrooms.py`
  constructs no `Publisher` at all. The connection-scoped flag now carries a 60s
  re-resolve window instead, so the lag is a stated constant rather than a dependency on
  an event that never fires. **AC-16's "both units" framing is stale**: the quoting
  dossier's successor left a five-key per-*type* rule, not a two-unit split, so the draft
  rule is written as "unquotable for every type". The reuse target `_subject_code` moved
  to `contexts/activities/domain/subject_code.py`, and `useChatroomSocket.ts:615` is now
  `:642`. **The new surface**: `2026-08-24-group-activity-submissions` added a second
  worksheet path — `ActivityPanel.vue:378` renders `SchemaForm` directly for a group
  proposal, bypassing `ActivityHost` — which Q-1 predates. Taken **into** scope at the
  user's direction rather than deferred.)
  The original entry, kept here for its detail:
  `2026-08-24-agent-readable-live-drafts` (feature, **approved 2026-08-24**).
  **Two things changed under it while it waited.** The three seams its Q-10 names for adding a
  runtime tool are all now occupied by a second example, so follow `present_observation`
  rather than only `start_activity`: `BUILTIN_TOOL_NAMES` has a third grant-sourced entry,
  `build_agent_tools` takes a second `(context, sink)` pair, and `_builtin_tools` now carries
  an `is_observer` flag threaded from `run_turn` — a role-sourced tool has a worked precedent
  where before there was only a grant-sourced one. And the activity feed grew a second row
  marker: a server-computed digest follows `::`, not the em dash, which its AC-16 prompt edits
  must not contradict. The original entry, kept here for its detail:
  `2026-08-24-agent-readable-live-drafts` — waiting on
  `2026-08-24-traceability-extraction-gate`, `2026-08-24-observer-presentation-blocks` and
  `2026-08-24-example-agents-quote-unit-two`.
  The quoting dossier was a **logical** prerequisite
  (its Q-10): its AC-16 writes the draft rule into prompts whose submission rule the quoting
  dossier splits by activity type, and written second it says the sharper thing — unit 2
  submissions became quotable, drafts stay unquotable in both units, because what governs a
  draft is not topic sensitivity but the author not having chosen to send it. The blocks
  dossier is an **overlap prerequisite**: both add a runtime tool through the same three
  seams (`BUILTIN_TOOL_NAMES`, `build_agent_tools`' signature, `_builtin_tools`). Lets a
  granted binding read a room's unsent composer and activity-worksheet text on demand, via
  `read_drafts`. Reported over the existing room WebSocket like `typing.start`, held **only**
  in Redis under a TTL, never in Postgres. **This is the most privacy-sensitive surface in the
  product and its §8 says so plainly** — in the example course the unsent text includes
  13-year-olds' accounts of distressing events. Default-deny per binding, disclosure on by
  default, codes never names, per-call audit by count. **Its single most important rule is the
  read-time gate** (AC-6): an activity type whose payload agents may not see has no readable
  drafts either, and the platform consent lock withholds every activity draft immediately.
  **AC-16 is in scope and is not a follow-up**: the shipped pack prompts forbid quoting a
  *submission* and say nothing about a draft, so this task edits all three prompts and the
  example guide. A build that ships the grant with AC-16 unticked is the one combination the
  dossier exists to prevent. Opens SRS chapter §32.

### From the 2026-08-24 group-submission work

- (implemented 2026-08-25; see the note under In progress)
  `2026-08-24-group-activity-submissions` (feature, **approved 2026-08-24**, SRS Delta applied)
  — the original entry, kept here for its detail; it waited on
  `2026-08-24-traceability-extraction-gate`, `2026-08-24-observer-presentation-blocks` and
  `2026-08-24-example-agents-quote-unit-two`.
  **The largest of the series.** Lets a project
  Member Group ([R13.28]) be the subject of an `ActivitySession`, via a proposal one member
  makes and the group votes on. The platform does **not** hard-code unanimity: an activity
  type declares the consent fraction in a new `group_config`, as two integers so the required
  count is exact integer arithmetic, and the shipped example uses 2/3.

  **Read its §4.4 before proposing a group variant of anything.** None of the four existing
  example types fits, and the analysis is not a preference: units 2 and 4 are first-person by
  construction, and unit 2's *stated teaching point* is that answers differ per person — TA's
  prompt says "不需要被統一成一種答案" and AA's says the spread is the unit's most valuable
  observation. A consensus answer erases the signal both agents exist to surface. Unit 4 is
  worse: group consent over one member's distressing event either publishes it or forces a
  fiction. So the task **adds** `six-hats-shared-case` (the hats applied to a shared scenario,
  which is de Bono's original group use) and edits none of the four.

  **Three things a builder needs.** Its migration relaxes `NOT NULL` on
  `activity_sessions.subject_user_id` and replaces it with a CHECK — AC-1 and AC-7 are
  `pytest.mark.db` because a CHECK and a partial unique index are invisible to the unit tier.
  **Take the revision number from `alembic heads`, not from any dossier**: this one and
  `agent-readable-live-drafts` share all three predecessors with no ordering between them.
  Its Q-4 defines rejection as *threshold unreachable*, not first-dissent, because the latter
  silently implements unanimity. And **`allow_member_groups` is mutually exclusive with
  `allow_project_members`** (`chatroom_service.py:39-43`, [R13.04]), so adopting group
  submission changes who can enter the room, not just who submits together — AC-18 puts that
  warning in the settings UI, and its OQ-1 records that a guest can never join a group
  submission at all.

## In progress

- `2026-09-04-guest-anonymous-session` (feature, **in-progress 2026-09-04**) — Phase 1 (backend
  core) complete on branch `guest-anon-phase1`. All 11 Phase 1 ACs ticked. Migration 0085,
  three new endpoints, guest JWT, WS auth, access shortcut, cleanup worker. Phase 2 (frontend)
  and Phase 3 (UX polish) are separate sessions on stacked branches. Nothing lists this slug
  in `depends_on`, so no row moves out of Blocked.

- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.
Removed on 2026-09-03 after implementation: `2026-09-03-observer-ui-defect-sweep` (all sixteen
findings of `docs/audits/2026-09-03-observer-ui-visualization/findings.md`, in three phases).
Nothing lists it in `depends_on`, so no row moved out of Blocked. No migration, no API contract
change, no SRS Delta — every finding restores documented behaviour or corrects text against
documentation that was already right.

**Three branches, stacked, none merged at close.** `fix/observer-ui-sweep-phase1` (PR #182, CI
fully green) ← `fix/observer-ui-sweep-phase2` ← `fix/observer-ui-sweep-phase3`. Each phase
branched from its predecessor rather than from `main` because §7.1 makes P1 and P2 serial and
because P2 had rewritten the `useObservations.ts` lines P3 needed; each PR targets its
predecessor's branch as base until #182 lands, after which GitHub retargets them.

**Four things a later reader needs.**

**Its most consequential change is the first WebSocket publish `chatrooms.py` has ever had**
(P1/F-1). No writer in that file or in `chatroom_service.py` published anything, so no client
could be told the room changed — and every invalidation in the conversation slice named
`['conversation','chatrooms']`, which does not prefix-match the singular `convKeys.chatroom`.
The new `chatroom.updated` carries the room id and nothing else, because the room channel has
no per-recipient filtering. Then D-7 split its audience: the room channel is used only when a
non-creator can see the difference, and the creator's other sessions are refreshed over their
own user channel — otherwise the frame's *existence* was an observer-existence oracle in a
room with disclosure off, which a pure guest could read. §9 records the one sliver that split
does not close (a disclosure toggle is announced to a guest for whom nothing visibly changes)
as an accepted risk.

**Two dispositions are deliberately narrower than their finding.** Q-3 fixes F-6 by reporting
"unknown" to viewers who receive no event feed rather than by building that feed (FU-1 owns
the feed). Q-8 corrects the two pack prompts without a migration: install copies
`system_prompt` into an `agents` row and is idempotent by agent **name**, with no update path
and no pack-version column, so the project's twice-used hand-edit note is the remedy again —
and FU-6 asks whether that should keep being true.

**Five ACs are ticked "not executed" rather than on a unit test, on one shared ground.** AC-2,
AC-5, AC-9, AC-10 (first half), AC-12 (first half), AC-15 and AC-18 each need a running stack —
two browser sessions, a NULL-creator row, a platform admin on someone else's room, a provider
key, or a real browser running the lazy mermaid/KaTeX imports. Docker was unavailable across
all three build sessions. In every case the unit tier verifies the halves and never that they
meet, and the dossier says which half is missing rather than ticking the box. FU-5 records
that no `frontend/e2e` spec covers the observer surface at all, which is why so many of these
are manual.

**Three `/code-review` passes and one `check-security` pass found work the phases themselves
had created**, all fixed in phase rather than deferred: D-6 (a draft-access route left
half-fresh by P1's own emit), D-11 (the release/delete emit duplication F-14 exists to
correct), D-12 and D-13 (a spurious watchdog verdict that nothing cleared, and an unread badge
that double-counted across an async gap), and D-16/D-17 in P3. Its FU-9 through FU-13 are the
ones judged out of scope.
Removed on 2026-09-01 after implementation: `2026-08-30-runtime-contract-integrity` (two dead typed-error
recovery branches now branch on the class the transport actually throws; provider seed is an independent
per-model capability, forwarded to Gemini and honestly disabled elsewhere). Nothing lists it in
`depends_on`, so no row moves out of Blocked. No migration. Branch `fix/runtime-contract-integrity`,
base `0060c45`, five commits, held back from `main` at the requester's instruction.

**Four things a later reader needs.**

**Freshness was perfect, which is unusual and worth saying.** `git diff 73125821..HEAD` over every
file the spec cites was empty, so the analysis was approved unchanged. The two same-file overlaps its
Q-2 predicted (`AgentDetailView.vue` with the graphrag blueprint, `turn_engine.py` with the
large-artifacts dossier) are still unbuilt, so whoever goes second still rebases.

**The dossier's central claim held up under tracing: the generated `ApiError` is unreachable, not
rare.** `transport/axios.ts:223-228` registers the same rejection handler on the bare `axios`
singleton the generated services call, so `parseProblem` converts every failure before
`core/request.ts:266,280` can raise its own class. Both branches were dead and both their tests were
green on a class nothing produces. Note the 422 path resolves to `ValidationError`, an `ApiError`
subclass — the `instanceof` still holds, which is why the fixtures use the real subclass.

**A self-audit found the new lint gate had a hole in exactly the place the original defect lived.**
The rule was first written inside the `src/**` config object, leaving `frontend/tests/**` and `e2e/**`
outside it — and `npx eslint tests/mocks/handlers.ts` answered *"File ignored because no matching
configuration was supplied"*, meaning that tree, which holds the MSW handlers and render helpers every
suite builds on, was linted by nothing at all. A guard that cannot see test fixtures is no guard
against a defect whose signature was a test fixture. D-2; fixed in `c1651f7`, and `frontend/tests/**`
is now linted for the first time, by that one rule. Bringing the rest of the rule set to it is FU-5.

**A `/code-review` pass after CI went green found two more, and both were about the new gate rather
than the change it guards.** The selectors pinned the exact barrel specifier, so
`@shared/api-client/core/ApiError` — a deep path that resolves through the alias just fine — slipped
past while the gate reported itself healthy; that is the second time in one task that this gate was
narrower than its own promise. More importantly, **"the generated `ApiError` is unreachable" is too
strong**: `axios.ts:194` converts only when the body carries a string `type`, so any non-problem+json
error response (nginx 413/502/504, or any layer in front of `register_exception_handlers`) falls
through `:201` and `core/request.ts:228,266` re-raises it as the generated class. The two repaired
branches are unaffected — a backend 422 and 404 are both problem+json — but the wording is now
narrowed everywhere it appeared, and FU-6 owns normalising those responses in the interceptor so the
strong claim becomes earnable. D-8.

**Two green tests were rewritten rather than left passing.** `test_gemini_forwards_top_p_and_ignores_seed`
would have stayed green through this change while documenting the opposite of the new contract, and
an `AgentDetailView` case asserted `seedHelp` on a fixture whose OpenAI model now correctly renders
`seedDisabledReason`. D-4 and D-5. Neither was an assertion weakened to pass.

Removed on 2026-08-31 after implementation:
`2026-08-30-identity-onboarding-policy-hardening` (the email-domain policy is a versioned, audited
PostgreSQL singleton behind a three-phase rollout; banned accounts cannot be handed activation
links; one Admin may create 60 accounts per 10 minutes). Nothing lists it in `depends_on`, so no
row moves out of Blocked. Migration 0084, two new Admin endpoints, three new maintenance commands.
PR #175, CI run `33363993395`, 23 of 23 checks green including `backend-db`. Its **[R19a.13]
amendment is still draft** in the dossier's §13 and has deliberately **not** been applied to
`REQUIREMENTS.md` — it needs explicit approval.

**Seven things a later reader needs.**

**A `/code-review` pass found five issues after CI went green, and four were the same shape: a
docstring or runbook promising something the code did not do.** `InvalidLegacyEmailDomainPolicy`
was raised and exported but never mapped, so it fell through to `internal`/500 — reachable at
*request* time, since `compatibility` re-reads the legacy triple per request and that is where
every deployment sits until an operator activates. The `rollback_frozen` branch bypassed the
wrapper that makes every other database read a typed 503. `_run_transition` caught only its own
error type while the transitions it wraps also raise store errors, so a Redis blip mid-rollback
would have printed a traceback at the worst possible moment. And §7a.6 listed three boot-blocking
legacy shapes when there are four — **the omitted one being the likeliest**, because the replaced
code applied no validation at all, so `localhost`, `*.example.edu` or a stray space may well sit
in a live deployment's Redis right now and would fail a boot that is fatal by design. The runbook
now carries a pre-upgrade `SMEMBERS` check. Fifth: the Admin form discarded in-progress edits on
any background refetch. D-10.

**CI's first run was red, and both failures were in the dossier's own new db-tier tests.**
`pytest.raises` shared an `async with` header with the session (it is a *sync* context manager, so
it raises only when the test runs — collection, ruff and mypy are all clean), and two autouse
fixtures raced over the process-wide Redis client, so from the second test onward the async one
reached a client bound to the previous test's closed loop. **A test tier that has never run is a
hypothesis, not a verification**: 27 db-tier tests were written on a host with no PostgreSQL or
Redis and both defects survived every local gate. D-9.

**Its AC-8 asked for something that cannot be built, and the build said so rather than passing it.**
The criterion claimed a rollout transition is observed by an *already-warm* reader on its next
request. A process-local cache hit performs no I/O by construction, so a maintenance command in its
own process cannot reach a serving process's memory. Deleting the shared mirror does make the phase
visible to the next reader that *consults* it; a warm reader converges when its snapshot expires,
bounded by the same 30 seconds. **Both paths are bounded identically; only "immediately" differs.**
D-3 records it, and the first version of the test had *hidden* it by calling `reset_process_cache()`
inside its own transition helper — asserting a property the production path does not have. The
generalisable half: when a test needs a helper the production caller cannot invoke, the helper is
the finding.

**A fatal startup step breaks every test that boots the app, and the stub that fixes it needs its
own guard.** The policy import is deliberately fail-boot (unlike the rate-limit primer beside it, a
policy has no compile-time default), which broke eight `healthz`/`readyz`/`metrics` tests.
`tests/conftest.py` now drops the step — and the first attempt at that patched the step's *name* in
`app.bootstrap.startup` and silently did nothing, because `app.main` binds `INITIALIZERS` by value
and the list holds function objects. Two tests now pin that the step is registered, ordered before
the best-effort primer, and propagates its failure; without them, deleting it would leave the suite
green while every registration 503'd. D-8.

**The response model must not reuse the request model's bounds.** Both shared one `Annotated` alias
carrying `max_length=1000`. The boot-time legacy import applies no count cap, so a deployment
arriving with more domains holds a row that bound cannot describe — and validating the *response*
against it turned the one screen an operator needs in order to shrink the list into a 500. Found in
self-audit, confirmed by construction, pinned by a regression test. D-5, FU-8.

**The write path never reads the cache, and that is load-bearing.** `EmailDomainPolicyService` goes
to the repository for both the GET and the version guard, so a poisoned mirror cannot influence
what an Admin sees or what the `UPDATE` matches. The security gate traced that explicitly; keep it
that way.

**§17 is the section to read before trusting the ticks.** CI closed most of it: the 20 db-tier
tests and 7 migration-schema tests all ran, `alembic upgrade head` applied 0084 against a real
database, and `SMAP_SCRATCH_DATABASE_URL` **is** set in the `backend-db` job so the migration test
ran rather than skipping. Two things remain unverified and a green CI does not close either.
**No rendered copy in either locale has been seen by anyone** — the component harness renders
`$t` as the key, so the locale files are checked only for `en`/`zh-TW` parity and the escaped
literal `@`. And **no second release has ever run beside this one**: every db-tier test drives
both sides from *this* build, so "an old replica enforces the mirrored legacy snapshot" rests on
the previous image's reader contract being what it is believed to be. That is the risk the whole
rollout design exists to manage, and AC-8 stays unticked for exactly that clause.

Removed on 2026-08-31 after implementation:
`2026-08-30-chatroom-approval-and-overlay-discoverability` (the room `approval.requested` event
carries the persisted timestamp, and the chatroom's three transient surfaces have one owner for
exclusion, focus and backdrop). PR #174. Its **[R24.32] amendment is now applied** at
`REQUIREMENTS.md:1974`, which is what closed the SRS gap that `b4b25d1`/`bdea016` left open; the
clause deliberately states the 768-1023 target and the shipped deviation in one sentence, and FU-2
removes the second half when that band moves.

**Three things a later reader needs.**

**The rollout fallback has consumers, and they were the defect.** Deciding that a timestamp-less
event gets an unparseable placeholder rather than a client clock is correct and is the whole point
of the change, but the placeholder is a value that flows: `ChatroomView.feedItems` had already been
taught to map it to the tail, and `ApprovalCard` had not — it rendered "Times out in NaN:NaN". A
sentinel is only as safe as the last thing that reads the field. D-7 records the repair.

**Reconciliation had no schedule.** `reconcilePending` is the only thing that retires the
placeholder, and it is called from the socket's reconnect handler alone, so on a healthy connection
nothing would ever have called it. The insertion site now kicks one pass, and only when it inserts a
placeholder. D-3.

**Two dims multiply.** `--overlay-backdrop` is itself `rgba(0, 0, 0, 0.45)`, so painting it at
`opacity: 0.2` — the literal reading of the UI document's "at 0.2 opacity" — composites to 0.09,
which is not a dim. `--overlay-backdrop-inline` now carries the strength directly and
`07-conversation.md:750` names the trap. D-2.

Removed on 2026-08-28 after implementation:
`2026-08-27-openai-responses-api-migration` (the OpenAI adapter posts to `/v1/responses`, so
reasoning effort and function tools compose on the platform's default provider for the first time).
Nothing lists it in `depends_on`, so no row moved out of Blocked. No migration, no API contract
change, no frontend source change. PR #170, CI run `33171817174`, 22 of 22 jobs green.
**Five things a later reader needs.**

**It was assess-first and the assessment is the durable half.** §5 answers six questions with
citations and is worth reading before touching any provider adapter, because most of it is not
OpenAI-specific: Responses thinks in *items* where Chat Completions thinks in messages, and every
other difference falls out of that. Its most useful finding is that the migration is
**net-simplifying**. `response.completed` carries the same response object the non-streaming call
returns, so the two normalisation paths collapse into one function and the Chat Completions
tool-fragment accumulator is *deleted* rather than ported. Option C ("migrate the non-streaming path
only") was rejected on its own premise: streaming is the half that got easier.

**`store` defaults to TRUE on this endpoint.** Omitting it leaves every turn's content with OpenAI
for at least 30 days; Chat Completions retained nothing by default. A migration that simply
translated field names would have changed the platform's data-handling posture silently, on a
product whose example course carries 13-year-olds' accounts of distressing events. `"store": false`
is unconditional and is asserted on both the invoke and the stream path. **The generalisable half:
when an endpoint changes, its defaults change with it, and a default is not a field you can notice
by diffing what you send.**

**D-1 is the correction the approved spec got wrong, and it is a lesson about who reads a flag.**
§6.4 said the two dead capability fields would keep their values and gain comments. That was right
for `uses_completion_token_field`, which the adapter reads directly and now doesn't. It was wrong
for `effort_conflicts_with_tools`, which is read by the *shared* gate
`CapabilityFlags.forwardable_effort` — deliberately left untouched — so a row that still declared
the conflict would have gone on dropping the exact effort value this task exists to deliver, while
`AgentDetailView.vue` went on disabling the control for the same reason. Cleared on all three
gpt-5.x rows. **A field is dead when nothing reads it, not when its author stops thinking about it.**

**The reasoning-item passthrough was taken into scope rather than deferred (Q-4), and that was the
right call for a reason worth reusing.** Responses asks that a reasoning model's own output items,
encrypted reasoning included, be replayed on the next request during function calling; the neutral
message shape had nowhere to put them. Deferring it would have shipped a quality regression on
every multi-round tool turn that **no test in this repo could see** — which is exactly the class of
debt that never gets paid because nothing ever goes red. The neutral assistant message gains an
opaque `provider_items` key: the turn engine copies it without reading it, only the adapter that
wrote it replays it, and the other two rebuild messages field by field so they cannot forward it
even by accident. It never reaches PostgreSQL.

**AC-15 and AC-16 are deliberately unticked and §17 says exactly what that costs.** No real key was
available (Q-5), so **nothing in this task has ever sent a request to the real endpoint**. Three
named unknowns remain, in bite order: whether function tools default to strict mode (the adapter
sends `"strict": false` on that reading, and getting it wrong fails the first real turn while every
`respx` test stays green), whether a non-reasoning model accepts
`include: ["reasoning.encrypted_content"]`, and whether the HTTP error envelope still carries
`type`/`param` — if not, errors still scrub safely but stop naming causes, silently undoing
`1d9a3da`. AC-16 is also the capability table's own FU-12, and one live session answers both.

Removed on 2026-08-28 after implementation:
`2026-08-27-provider-model-capability-table` (one per-model capability record replaces three
per-provider dictionaries — `CHAT_MODEL_CATALOG`, `DEFAULT_CHAT_MODELS`, `CONTEXT_LIMITS` — and
five per-model regexes spread across the OpenAI/Anthropic/Gemini adapters; the agent-config form
now disables a control the selected model refuses). **It unblocked
`2026-08-27-openai-responses-api-migration`**, moved to Ready above. Migration 0083 (widens
`agent_effort` to seven values), no new API endpoints, `GET /api/model-catalog`'s response shape
changed. **Four things a later reader needs.**

Not from an audit: this and its sibling `2026-08-27-openai-responses-api-migration` came from
diagnosing a staging agent (`結書`) that failed every turn for two days with
`provider_exhausted:request_rejected`. Four commits landed ahead of the specs and are already on
main: `1d9a3da`, `e16bc90`, `b6d7abe`, `98838dd`.

**AC-6, AC-11 and AC-15 are deliberately unticked, at the user's explicit direction** (2026-08-28):
all three need a real provider key against a live endpoint — the reconciler run
(`smap maintenance reconcile-model-catalog`, built and unit-tested but never run live) and the
toolful `gpt-5.4` + `reasoning_effort` omission check from §4.3a — and none was made available
this session. `model_specs.py`'s table is re-expressed from the pre-existing dictionaries/regexes
this task deletes, not re-verified against current provider lineups; `source_url`/`verified_on`
carry the same "2026-06" provenance the replaced comment already claimed rather than a fresh date,
so the dossier does not overstate freshness. Recorded as FU-11/FU-12/FU-13, all blocking a future
close before those three ACs can tick. See the dossier's Deviation Log (D-1, D-2) for the full
reasoning, including why the shipped adapter keeps `e16bc90`'s omission behaviour rather than
switching to the alternative (`reasoning_effort: "none"`) pending that verification.

**The security gate found, and this build fixed before commit, a real gap in the design as
approved.** All three adapters gated effort-forwarding on the coarse `accepts_effort` boolean
rather than on membership in the model's own `effort_values` list. Since `agent.effort` is stored
independently of `model_id`, an authorized project member could `PATCH` an agent to an
out-of-range effort value (one valid under the widened seven-member enum but not listed for that
specific model) and reproduce this task's own incident class through the one channel the widening
opened. Fixed in all three adapters before commit (D-5); the same gate found a second,
**pre-existing** issue — `adapters/gemini.py` splices `model_id` unsanitized into the request
path — confirmed untouched by this diff and left as FU-14 rather than folded into an unrelated fix.

**A background quality-audit fork independently reached the same AC-15 design conclusion** (keep
`e16bc90`'s omission behaviour, matching AC-13's literal wording and the only variant with
production evidence behind it) and wrote the dossier's Deviation Log entries directly rather than
only reporting — reviewed and accepted after independent verification of every factual claim in
it, per this project's "verify before trusting documentation" standard.

**A `/code-review` pass over the open PR found 9 more Introduced issues, all fixed and pushed
as a second commit (D-6).** Two worth a later reader's attention: the `model_specs.resolve_spec`
lookup had gone case/whitespace-sensitive (the five deleted regexes were not), and the o-series
(`o3`/`o3-mini`) was silently floored to Q-2's conservative default because it had never been
catalogued — both restored, re-derived from the same deleted regexes as the rest of the table,
not freshly verified. CI re-run in progress on the second commit; see the dossier's D-6 for the
full list (`AgentDetailView.vue` clearing stale effort/sampling on a mid-session model switch,
the reconciler's provider-mismatch guard and pagination, FU-14 closed rather than left deferred).

Removed on 2026-08-25 after implementation:
`2026-08-24-agent-readable-live-drafts` (a granted binding may read a room's unsent
composer and worksheet text on demand, through `read_drafts`; the text lives only in
Redis under a 900s TTL and never reaches PostgreSQL). Nothing lists it in `depends_on`,
so no row moved out of Blocked. Migration 0082, one new route, one new runtime tool, an
SDK contract extension. **Five things a later reader needs.**

**Its two most important guarantees were both wrong on the first attempt, and both were
found by a gate rather than by a test.** The security gate found that a participant could
**forge another participant's attribution header** — the tool renders a server-written
`u:CODE …` line then the raw draft, so a student typing a look-alike header into their own
composer got their words attributed to somebody else's code. The code is not secret: the
typing indicator renders exactly `uid[:8]` on everyone's screen. The fix is structural —
every content line carries a `| ` prefix and a header never does — and then `/code-review`
found *that* incomplete, because `split("\n")` honours one of seven line terminators and
CR, VT, FF, U+0085, U+2028 and U+2029 all walked straight through it. **The generalisable
half: when a format's safety rests on "content can never look like a delimiter", the
delimiter set is the whole claim, and `splitlines()` is not `split("\n")`.**

**A cap that counts the wrong population is a denial of service against the person it was
protecting.** `MAX_USER_ENTRIES` was added because the byte budget bounds storage and not
key count. Its first version counted `SMEMBERS` — but the index deliberately outlives its
entries and is reconciled only on the read path, and closing a browser tab fires no unmount
hook, so eight stale members would accumulate in ordinary use and then silently refuse the
participant's own chat draft forever. It counts live values now and prunes the dead ones,
which costs nothing extra because the byte budget already fetches them.

**Two consumers reached into `contexts.conversation.infrastructure` directly** — a route
below the facade, and one context's application layer touching another's infrastructure —
sitting inches from code doing it correctly (`PresenceTracker` via `interfaces`,
`activity_tools` via the facade). `lint-imports` cannot see this: its contracts enforce
domain purity, not the application/infrastructure direction. That is the second time this
blind spot has produced a finding in this series; an AST test now guards this instance.

**`chatroom.agents_changed` does not exist**, and the dossier's §5.1 was built on it. The
grant is re-resolved on a 60s window instead. Worth knowing before writing anything else
that wants to react to a settings change on an open socket: **no room event is published
when a binding's grants change.**

**Every criterion closed, and AC-15 on a real run**: CI `32862687028` on PR #167, 22 of 22
jobs green including `backend-db`, `backend-wiring` and `frontend-e2e`. It took two runs —
the first failed `backend-lint` because the task's last edit was followed by `ruff check`
and `mypy` but not `ruff format`. **Running two of the three mechanical gates is running
none of them**, since the one skipped is the one that fails.

**What no gate covers is in §17 and is the part to read.** The browser pass never happened
— two sessions, the chip in both places and both disclosure states, the Redis keys checked
after a send and a submit — and **no real model has ever called `read_drafts`**, because
`fake_provider.py` cannot produce an agent turn. Every claim about how a model reads the
`| ` prefix rule therefore rests on the description being written clearly, which no test
can establish. The example guide's dry-run checklist carries the corresponding item.

Removed on 2026-08-25 after implementation:
`2026-08-24-group-activity-submissions` (a project Member Group may be the subject of an
`ActivitySession`, through a proposal one member makes and the group votes on; the consent
fraction is declared by the activity type, not by the platform). Migration 0081, four new
room-scoped endpoints, one new example type. **Four things a later reader needs.**

**A participant contract is not the owner's contract, and §6 forgot which one the panel
sees.** The dossier enumerated the four models that gain `group_config` and missed
`ActivityTypePublicOut` — the only type shape a participant ever receives, and the one
`ActivityActivationOut` embeds. It also assumed a student could read the room's bound
groups, which is `PROJECT_MEMBER_MANAGE`-gated. Both were added at the start of the
frontend milestone (D-10). The lesson generalises: **when a dossier says "the panel shows
X when the type carries Y", check that Y is on the shape the panel is handed**, not merely
on the model with the same-sounding name.

**Group mode has three client states, not two** (D-11). "Has a group" and "the read has not
answered yet" both read as false on any `canPropose`-shaped boolean, and conflating them
shows a group participant the *individual* worksheet until the request lands. Any surface
that switches on a server-answered capability wants the same third state.

**The room broadcast is counts, and must never become authorization.**
`activity.proposal.*` reaches every participant, including members of groups the reader may
not see. The client store therefore updates only a proposal the authorization-narrowed HTTP
read already returned and refuses to insert an unknown one, recording its group id so the
composable can decide whether re-reading is that caller's business. A client that trusted
the event would have rendered another group's vote from a payload carrying no evidence it
was entitled to it.

**Its AC-18 warning is the one guard against a confused first setup.** `allow_member_groups`
excluding `allow_project_members` ([R13.04]) is deliberate, so nothing can prevent the
lockout except saying so at the moment of the change — which is why the confirm lives in
`useChatroomSettings.setFlag`, before the patch, and covers every caller rather than one
view. FU-5 stands: the group flow has never been run end-to-end (it needs three sessions in
one room and a full compose stack), so the four routes are covered by unit and service tests
and by nothing that exercised them against a running server.

Removed on 2026-08-24 after implementation:
`2026-08-24-observer-presentation-blocks` (an observer agent assembles its analysis from a
closed set of platform-defined blocks through one structured tool call; the three quantitative
kinds are filled in by the server). **It unblocked two dossiers**, both of which listed it as
their last unmet dependency: `2026-08-24-agent-readable-live-drafts` and
`2026-08-24-group-activity-submissions` move to Ready. Migration 0080, one API field, one new
runtime tool. **Six things a later reader needs.**

**Its §5.5 was the correction that made the feature work, and its own claim was still wrong.**
The spec caught that a text-only empty-turn guard discards every block from a model that
delivers its analysis as structured blocks and says nothing in prose — the ordinary shape of
the feature. It then argued the widened guard is safe because the blocks serialise to a
non-empty `content_md` "by construction". They do not: `minLength: 1` accepts a single space
and `schema_violations` strips `pattern` outright, so a whitespace-only prose block is
schema-valid and renders to nothing. The guard now tests the **serialisation** rather than the
sink, and the tool refuses such an array on its own (D-4). **An invariant asserted as
"by construction" is worth checking against the validator that is supposed to enforce it.**

**The safety argument is structural, not a convention, and that is the part to preserve.** A
computed block's schema branch declares no value property and closes with
`additionalProperties: false`, so a call carrying its own counts is rejected before `invoke`
runs; the server fills those fields and stamps `server_facts` on them, so a computed block
cannot be mislabelled by its caller either. A participant can persuade the agent to *include*
a coverage figure and cannot change a number in one, because the model is never asked for one.
Every denominator is submissions counted, never a share of a class.

**AC-18 needed a fact the spec said would not exist** (D-1). Nothing records whether a stored
`agent_digest` came from a validator `detail` or from the payload dump — one `TEXT` column
holds both. It is derived now, by rebuilding the deterministic fallback and comparing, which
is exact for rows written before the distinction existed; a backfilled column could only have
guessed, and the wrong guess is the unsafe one.

**The computed digest took a new row marker and all four shipped prompts moved with it**
(D-2). TA, SA, AA and DA all state the em-dash rule verbatim, and it becomes false for the
example's four types the moment they adopt `filled_count_coverage`. The em dash keeps its
meaning; a computed digest follows `::`. The security gate then found the note had to say
**which** marker counts: a participant whose quotable answer reaches the row can write `::`
into it, so the rule is "one marker per row, the first one on the line", mirroring the clause
the participant-text note already carried.

**The quality gate found the layering, not the linter.** `submission_repo` was importing an
application module, and `attempt_summary_rows` was handing raw SQLAlchemy `Row`s to the
application layer. `lint-imports` passes on both: its contracts enforce domain purity, not the
application/infrastructure direction. `agent_digest` and `subject_code` now live in `domain/`,
which is where two pure rules both layers need belong anyway.

**AC-15 is deliberately unticked and §17 of the dossier says exactly what was not run.** The
`db` tier's 10 tests, `alembic upgrade head`, and `check:openapi-drift` all need a Docker
daemon this host does not have; the drift check's two steps were run by hand and their outputs
committed, which is not the same as the gate going green. The browser pass was not performed.
Removed on 2026-08-24 after implementation:
`2026-08-24-traceability-extraction-gate` (`docs/traceability.csv` is generated by
`scripts/traceability.py`, `repo-gates` rejects a commit where it disagrees with
`REQUIREMENTS.md`, and every `[Rxx.yy]` cited outside the SRS now resolves). **It unblocked
`2026-08-24-observer-presentation-blocks`**, moved to Ready above. Docs, one script, one
test file and one CI step; no migration, no API change, no runtime code. **Four things a
later reader needs.**

**The spec's own §4 measurement was wrong, and the way it was wrong is the lesson.** §4
enumerates three ID shapes and warns that a naive `\[R(\d+\.\d+)\]` misses two of them. It
missed a **fourth** itself: a letter suffix on the *chapter* number — `[R11a.01]`,
`[R11a.02]` and `[R19a.01]`-`[R19a.13]`, 15 defined requirements, four of them cited from
live code. Built as specified, the CSV would have been complete-by-construction while
omitting them, and the citation check would have been blind to those four citations. The
gap was found by re-measuring at build start rather than trusting the spec's numbers, and
**the true figure is 421 defined requirements, not 389** — 15 from the miss and 17 from the
other 2026-08-24 dossiers' SRS deltas landing after §4 was measured.

**The backfill was 115 rows, not 83, and the summary column is now derivable.** All 306
existing rows were regenerated per Q-2. 186 of them reproduce byte-for-byte from the
mechanical rule; the 120 that differ had lost apostrophes, flattened em-dashes to hyphens,
dropped `§` and `≤`, left `*italic*` markers in, or simply gone stale against an SRS
sentence that had been rewritten under them. Per D-2 there is **no truncation** — no row
ends in `...` any more, and the longest is `[R12.03b]` at 1502 characters.

**AC-6 paid for itself twice.** Reviewing the regeneration diff row by row found two parser
defects, not two bad SRS sentences: a fenced code block directly beneath a definition was
being swallowed into the summary (`[R9.13]`, `[R12.11]`, `[R24.18]`), and emphasis wrapping
a code span survived unstripped (`[R24.13]`) because splitting the line on code spans puts
the two `**` markers in different fragments. Both are corrections to the stated rule, so no
SRS text was edited. The rule that made this work: **fix a bad summary in the SRS sentence,
never with an exception in the script.**

**Every criterion closed, both CI-dependent ones on real runs.** AC-10 is run `32747505420`
(`repo-gates: completed success` on main). AC-12 is PR #163 — a throwaway `[R27.99]` with the
CSV left alone, closed unmerged — where `repo-gates` failed at exactly one step,
`traceability.csv matches REQUIREMENTS.md`, while both sibling gates passed. The gate was
also mutation-probed locally before landing: red on a deleted row, red on an invented
`[R99.99]` citation, green once reverted.

**A unit test can break a CI tier that never runs it** (D-9), and this shipped. `backend-db`
and `backend-wiring` bind-mount only `backend/` over `/app`, so `scripts/` is outside those
containers; the new test resolves the script through `parents[3]` and **executes it at import
time**, so both tiers died at collection with `FileNotFoundError` and lost 7502 and 7576
deselected tests. Six sibling files use `parents[3]` too and none of them broke — they build
the path at module level but read it inside a test, so marker deselection removes them before
the missing file matters. **Collection runs before deselection; import-time I/O is what
forfeits that protection.** The guard is a module-level `pytest.skip(...,
allow_module_level=True)`, verified by rebuilding the mount layout locally in both states.
Removed on 2026-08-22 after implementation:
`2026-08-22-visual-refinement-phase3-verification-and-debt` (the parity baseline is
regenerated and CI is no longer red on it; the product phase 2 shipped has now been
looked at across 164 combinations; and phase 2's FU-8 to FU-12 are each closed).
Nothing lists it in `depends_on`, so no row moved out of Blocked. Frontend, CI and docs
only; no migration, no API change. **Five things a later reader needs.**

**The visual pass found six defects and none of them was a typeface regression.** Inter
caused no truncation anywhere — sidebar nav labels, `STable`'s `nowrap` headers and
`SBadge` pills were clean in all 164 combinations, which is exactly the question phase 2's
AC-3 was supposed to answer. What it found instead is that **375px had never been looked
at**: a page title rendered in a 13px box (the single letter "A"), another in 0px with its
action row running 267px off-screen, the landing page scrolling sideways, the chatroom
header cut mid-word. All six predate phase 2. Measuring found four of them, looking found
the other two — the two that *wrap* rather than clip, which no measurement sees.

**`1fr` and `min-width: 0` are the two shapes to recognise.** A `1fr` grid track is
floored at `min-content`, so it is sized by its widest descendant rather than the space
available; `minmax(0, 1fr)` is what people mean. `overflow: hidden` (which `truncate`
sets) makes a flex item's automatic minimum size 0, so a label with it does not ellipsise
when squeezed — it disappears. Both failure modes are invisible on a desktop window,
which is why they survived three visual dossiers.

**A gate that cannot fail is worse than no gate.** Phase 2 closed FU-8 as "proven by
construction" because its font assertions ran against Vite, which sends no CSP header at
all. The new `frontend-csp-font` job serves a built `dist/` behind the CSP **extracted
from `smap.conf`**, asserts the response header before anything else, and was run in both
directions before landing — red under `font-src 'none'`. The same discipline applies to
the new narrow-viewport spec, which was verified by reverting the fix.

**FU-11 did not reproduce, and the more useful finding is why.** Eighteen runs under
six-way CPU load all passed. The timeout moved anyway, with the measurement that justifies
it (slowest test 1319ms against a 5s default). But three full local suites during
close-out failed 1, 0 and 2 tests and never the same ones — the thin headroom is
host-wide, not file-specific. That is FU-7, and raising timeouts one file at a time is a
treadmill.

**Two things are deliberately left unticked.** AC-5 has two of seven backdrops unobserved
(`.s-card__footer` has no focusable control on any C-0 surface; the dropdown's
keyboard-opened ring resisted three harness approaches) and AC-10 cannot claim "no longer
reproduces" for something that never reproduced. Both are recorded as FU-5, FU-6 and FU-7
rather than closed by reasoning — which is the habit this whole dossier existed to break.
Removed on 2026-08-22 after implementation:
`2026-08-22-safe-area-uncovered-top-surfaces` (the impersonation banner, the toasts and
the top bar all inset exactly the edges they meet, and the enumeration that missed them
now refuses to let a new top-anchored surface go unclassified). Nothing lists it in
`depends_on`, so no row moved out of Blocked. Frontend only; no migration, no API change.
**Five things a later reader needs.**

**Its approved design did not work, and the reason will catch the next person too** (Q-8).
A custom property substitutes its `var()`s at computed-value time **on the element that
declares it**, so a descendant that redefines an input inherits an already-resolved total
and cannot change it. `--topbar-height-total` is declared on `:root, .app-root` for that
reason — one `calc()`, two subjects — and it was measured in Chromium before the design
moved, not argued from memory: `100px` where `56px` was intended, and `56px` once the
declaration was repeated on the overriding element. **If you build a token out of another
token and expect an override to reach it, check where it resolves.**

**The sibling sweep cleared the one file that was also broken** (Q-9, D-2). §6 asked "does
this surface inset itself" of `SNetworkBanner` and got yes. The right question was what its
`--below-topbar` offset *assumes* — it was arithmetic over `--topbar-height-total` from a
`position: fixed` origin, so it painted across the top bar during an impersonation session
and would have got worse under the conditional inset. It now sits in a zero-height
`position: relative` anchor below the impersonation banner and is positioned `absolute`, so
"below the top bar" is measured from a flow position and no arithmetic can drift. **The
generalisable half: arithmetic over a height is what produced the bug; adding a second
number would have repeated it.**

**D-5 is the finding a post-close `/code-review` caught, and it is the dossier's own
reproduction.** §7 specified `padding-top` alone, and §4 step 3 says "rotate to landscape:
it renders under the cutout". On a notched device in landscape `safe-area-inset-top` is
`0px` and the sensor housing becomes a left/right inset, which this full-bleed row never
cleared — the exact half-protected surface the per-edge sweep exists to catch, and with
`['top']` in `INSET_SURFACES` the sweep could never have flagged it. Three edges now, sides
`max()` and top additive.

**D-6 is a testing lesson worth carrying.** `App.test.ts` threw `No 'queryClient' found in
Vue context`, which was read as "App.vue is mounted above the QueryClient provider" and
answered with a narrower slice export. False: `main.ts:56` installs `VueQueryPlugin` with
`app.use()`, an app-level provide the root component resolves like any other, and the test
simply never installed it. **A test harness that differs from `main.ts` is not evidence
about the application.** The export was reverted and the test got the plugin; FU-5 asks for
something that stops the harness drifting again.

**AC-6 is deliberately unticked and nothing in CI can close it.** Headless Chromium emulates
no display cutout. What was done instead is the dossier's own §4 simulation — the shipped
rules with `env()` replaced by a constant `44px`, measured at 390x844 in both states: banner
0-79px with its text at y=52 (8px of interior padding survives the strip), top bar
`padding-top: 0px` at exactly 56px with a 56px grid track and no empty band. That proves the
cascade and the geometry and nothing about a real inset — **the landscape insets and the
toaster's rendered offset have been reasoned about and not seen.** FU-2 is now the
load-bearing follow-up, because AC-6 is the only written form of that device check and it
leaves with this dossier.
Removed on 2026-08-22 after implementation:
`2026-08-21-visual-refinement-phase2-identity-and-depth` (the product has a typeface, one
neutral axis, surfaces that are actually layered, three rule weights instead of one, and a
pressed state where `:active` had appeared nowhere in `frontend/src`). Nothing lists it in
`depends_on`, so no row moved out of Blocked. It **applied an SRS Delta** at approval and
**amended [R24.28] again at build time** (its D-10). Frontend and docs only; no migration,
no API change. **Five things a later reader needs.**

**It closed with two acceptance criteria unticked, by the user's explicit scope decision,
and one of them leaves CI red.** That is not a formality. `frontend-e2e` fails on
`00-visual-token-parity` — one job, one spec, 21 failures all in it — because the baseline
predates the restyle and phase 1's D-17/D-18 established it is only meaningful when
regenerated on a freshly seeded stack. **And AC-3 was never performed at all**: no overflow
pass at any viewport in any locale, no traversal of the surface set for a mismatched focus
ring, nobody opened the landing page. Inter runs wider than the stack it replaced, so this
is a visual identity that has been measured thoroughly and seen barely at all. Both are
owned by `2026-08-22-visual-refinement-phase3-verification-and-debt` in Ready above.

**Two mid-build decisions changed the token vocabulary past the approved spec**, both taken
with the user. AC-9's border clause was unreachable as written — 3:1 on every container
edge makes the product a grid of mid-grey lines — so WCAG 1.4.11 was applied where it
actually governs, via a third weight `--color-border-strong` for form controls only. And
Q-12's retained `#fff` was a live AA failure: white on the dark theme's accent is 2.54:1
and on its danger 2.77:1, on the most-used control in the product, so `--color-on-accent`
and `--color-on-danger` are theme-aware now.

**D-11 is the one to read before adding a colour token.** A post-build `/code-review` found
two tokens shipped byte-identical to a surface they are drawn on: dark
`--color-border-subtle` equalled dark `--color-surface`, so every interior rule on a table
header, card footer or editor gutter *vanished* rather than lightened in dark mode; and
light `--color-neutral-tint` equalled `--color-canvas`, so every badge at SBadge's default
variant had no pill. The contrast test could not see either, because it measured the border
weights against `--color-bg` alone and the tint against its own text. **A budget that
measures a token against one background cannot see a collision with any other** — and the
surface roles this dossier introduced are what made that reachable. Both guards exist now
and were mutation-probed.

**D-12 is a Windows trap that will recur.** A bulk `Get-Content -Raw` + `WriteAllText` pass
over seven files decoded UTF-8 as the ANSI codepage and wrote it back lossily, turning four
em-dashes into `??`. Comment-only this time; the extent was established by diffing the
commit for lines it had no business touching. Use `[System.IO.File]::ReadAllText` for bulk
rewrites, or do them one Edit at a time.

**Three CI gates cannot run on this host at all** (D-9) — `check:bundle-size`,
`check:type-coverage`, `check:boundaries-enforced` are bash scripts, the same class of
blocker phase 1 hit with `check:openapi-drift`. All three went green on CI run
`32561272600`, which is the only thing that could close them.
Removed on 2026-08-22 after implementation:
`2026-08-21-visual-refinement-phase1-token-adoption` (the design tokens are load-bearing:
811 of the 985 type and spacing declarations in `frontend/src` now name a token, and both
`docs/UI/` specifications are written in token names). **It unblocked
`2026-08-21-visual-refinement-phase2-identity-and-depth`**, moved to Ready above — that
dossier is almost entirely token-*value* edits, which reached nothing until this landed.
Frontend and docs only; no migration, no API change. **Six things a later reader needs.**

**Its §2 undercounted the work by three times, and the reason generalises.** The counts
(109 type, 168 spacing) were measured inside `shared/ui/`, while AC-3's sweep covers `app/`
and `slices/` too. The real figure is 985 declarations across 220 files. If a criterion
names a scope, measure the criterion's scope, not the section that motivated it.

**Q-3's conservatism did not survive the wider scope, and three scope calls were taken with
the user before any code moved** (D-1 to D-5): 55 declarations sit on the 2/6/10px
half-steps and 12 more on line-heights of 1 and 1.4, none of which had a token, so the AC-3
exemption list would have run to ~115 entries and the sweep would have asserted nothing.
Five token families were added, all exactly equal to the literals they replace.
`main.css`'s own `@layer base` was tokenised too — the file that declares the vocabulary was
also ignoring it, and a phase-2 change to the ramp that skipped `h1`/`h2`/`h3` would have
left every heading at the old scale.

**D-14 is the one to read before writing another baseline-comparison spec.** The parity spec
was numbered `24-` and ran last. It reported 48 vanished signatures and 10 value
differences, **none of them a CSS change**: the suite posts messages so the chatroom empty
state stops rendering, it creates an invite so the invites empty state goes, and
`.s-empty-state` declares no font-size of its own, so where it lands decides what it
inherits. It is `00-` now. A baseline can only be compared against the data state it was
captured in.

**The harness was self-checked before it was trusted** (D-11): capture the baseline, then
immediately compare unmodified code against it. That found three defects that would each
have shipped as an intermittent CI failure — `margin: auto` centres against content width;
`span.sr-only` renders at 12px/600 inside a button and 16px/400 beside one, so first-in-DOM
order moved with the data; and a visible `<main>` is not a settled page.

**Two mechanical checks did more than the test suite could.** Every changed line in all 118
`.vue` files was proven to sit inside a `<style>` block (a substitution landing in a
template would not necessarily fail anything), and every `var(--token)` in `src/` was proven
to resolve — an unresolvable `var()` falls back to the initial value in silence. The latter
found **five colour custom properties that are referenced and never declared anywhere**
(FU-5), pre-existing and phase 2's to fix.

**AC-7 is closed by CI run `32515930960`, 23 of 23 jobs green**, but it took four runs and
each failure earned its keep. D-17: the parity spec failed on CI while passing locally,
because its missing-signature check tested DOM presence — something no CSS value can change
— and was instead exquisitely sensitive to how much data a stack holds. D-18: regenerating
the baseline on a pristine `smap_test` then exposed three more harness defects the saturated
local stack had masked, including a capture path that overwrote the committed 82-slot file
with 4 slots after a worker restart. One red was not this dossier's at all —
`22-layout-contract`'s `/invites` stub had been failing on `main` beforehand, its glob
matching no endpoint. **The lesson worth carrying: a baseline artifact is only as good as
the data state it was captured in, and a developer stack drifts from CI's with every suite
run.** Two pre-existing e2e fragilities remain as FU-10 — nothing raises the invite rate
limit for a suite run, and `18-delegated-activity-control`'s two tests share one seeded room.
Removed on 2026-08-21 after implementation:
`2026-08-19-mobile-viewport-and-breakpoints` (the app is sized against the viewport the device
actually shows, the breakpoints agree with themselves at their own boundaries, the mobile
drawer contains its sidebar, and the agent action bar stops covering the end of the form).
**This completes the four-dossier overlap chain** from the 2026-08-19 page-presentation audit
(`transient-feedback-channels` -> `shared-overlay-and-shell-defects` ->
`content-area-spacing-and-scroll-contract` -> this one). Nothing lists it in `depends_on`, so
no row moved out of Blocked. Frontend only; no migration, no API change. **Five things a later
reader needs.**

**The browser pass happened and was mutation-probed** (§12a) — which breaks the streak this
area had been running. Three fixes were reverted one at a time and produced exactly five
failures, each attributable to its mutation; the three tests that stayed green under all three
are correctly independent of them. The `sm` boundary was walked from both sides: measured
radius `0px`/shadow `none`/wrapper `none` at 479px and `8px`/present/`420px` at 480px.

**FU-9 is the one that outlives this task, and it is not small.** The build **already emits
media-query range syntax for every query in the app** — 30 `@media (width<=N)` blocks in
`dist/assets/*.css`, including widths this dossier never touched. There is no `browserslist`,
no `build.target` and no lightningcss `targets` anywhere in `frontend/`, so Vite and Tailwind
v4 apply their default modern target and Lightning CSS rewrites `max-width` on the way out.
That makes this dossier's own Q-7(b) premise false: it refused to *author* range syntax
because it needs Safari 16.4 against a stated floor of iOS Safari 16.2. So either the floor at
`11-responsive-a11y.md:346` is wrong, or **every media query in the product is inert on iOS
Safari 16.2–16.3**. Pre-existing and unrelated to the boundary fix, but it wants a
browser-targets decision, and it also answers the question FU-4 had parked.

**D-10: the e2e spec had three defects only running it could find**, and each would have
shipped as a silent CI failure. The generalisable one is now in
`frontend/.claude/skills/verify/SKILL.md`: **`toBeVisible()` does not mean "finished
animating"** — `SDrawer` is visible from the first frame of its 300ms slide while still
`translateX(-100%)`, so a panel measured then reports `x = -272` and every geometry assertion
after it is off by a panel width. The other two are there too: tab panels are `v-show`, so
`.last()` on a shared selector picks a hidden element whose `boundingBox()` is `null`; and
account routes are `/account/*`, where a wrong route renders a 404 view that still has a
`<main>`.

**`--topbar-height-total` now exists** (D-7) and both `AppShell`'s first grid track and
`AppTopBar`'s height read it. It is declared in a plain `:root`, **not** in `@theme`: that
block is Tailwind's token source and its values are processed at build time, while this one
carries an `env()` that must reach the browser intact.

**A post-close `/code-review` found two regressions this task shipped** (D-12), and both are
the kind that recur. `max-width: 100%` on `.sidebar` was commented "inert on desktop" and was
not: `AppShell` tweens the sidebar track 260px -> 0 over 300ms with the aside still visible,
so the nav **reflowed** through every collapse instead of being clipped — measured
`260, 169, 66, 19, 1, 1`, and it fires on every navigation into or out of a chatroom, not just
a manual toggle. And `SNetworkBanner` was a **third consumer** of the topbar height that never
got migrated to `--topbar-height-total`, whose own comment claims "this is the one place that
number lives" — it sits at `--z-banner` (350) over `--z-topbar` (200), so the drift paints the
banner across the top bar. If you add a token to deduplicate a number, grep for every consumer
before writing that comment.

**Three more were `viewport-fit=cover` exposing surfaces Q-5 never enumerated** — Landing's
nav, the skip link, the banner's unauthenticated position. The lesson is in the test: T-1(b)
asserted an inset appeared *somewhere* in each file, so a surface insetting two of four edges
passed as protected. It now asserts **per edge**.

**AC-3, AC-4b and AC-6 are deliberately unticked** and always will be: headless Chromium has
no collapsing URL bar, no virtual keyboard and no display cutout, so `dvh`, the
`visualViewport` inset and every `env(safe-area-inset-*)` are identically inert there. The
safe-area work in particular is proven only at the level of "the declarations exist" — a
source scan asserts the meta and all six surfaces together so neither half can ship without
the other, but **nothing automated has seen an actual inset render**. Confirm on a notched
device, portrait and landscape.
Removed on 2026-08-21 after implementation:
`2026-08-19-content-area-spacing-and-scroll-contract` (34 view roots stop duplicating the
shell's padding, 23 of them stop nesting a second `<main>`, and navigation gains the
scroll-reset contract it never had). **It unblocked
`2026-08-19-mobile-viewport-and-breakpoints`**, moved to Ready above. Frontend only; no
migration, no API change. **Four things a later reader needs.**

**Its Q-14 ships `vh` deliberately, and FU-8 is the pairing that must not be forgotten.** F-45
belongs to `mobile-viewport-and-breakpoints`, sequenced after it, so
`AgentDetailView.vue`'s `lg:h-[calc(100vh-3.5rem-3rem)]` matches the shell it shipped against.
Whoever moves the shell to `100dvh` must move that one line in the same change, or a smaller
F-51 comes back on mobile.

**Two of its own decisions were wrong and were corrected under CI.** Q-5 originally specified
`scrollTo({ top: 0 })` on the reasoning that jsdom's `scrollTop` setter is inert — both halves
measured false, and the specified line would have thrown in every navigating unit test; the
reset is `contentEl.value.scrollTop = 0`. And its first F-26 implementation titled the pending
page header with the loading string, which put a status string in the `h1` and **took two
pre-existing e2e specs down** (`15-tenancy-keys-mgmt.spec.ts`'s renames read the entity name
from the level-1 heading). D-7 replaced it with a header-shaped skeleton. The invariant worth
carrying forward: **on a detail page the `<h1>` is the entity's name, so an `<h1>` existing
means the entity has loaded** — three view tests now assert no `h1` during the fetch.

**Its §12a records three CI diagnoses of one failing assertion, two of which were wrong.** Worth
reading before writing a Playwright assertion about scroll position: `click()` scrolls its
target into view first (the repo's own `frontend:verify` skill says so), and a content-height
change makes the browser clamp `scrollTop` without ever rebounding.

**`.app-shell` sizes from `flex: 1 1 0px`** (`shared-overlay-and-shell-defects`'s D-10), and the
reason the basis must be a length rather than `flex: 1`'s `0%` is exactly what this dossier's
scroll contract depends on.

Also removed on 2026-08-21:
`2026-08-19-shared-overlay-and-shell-defects` (the impersonation banner reserves its own
space, a render error keeps the shell, an authenticated 404 keeps its chrome, and four
shared overlay primitives that looked wired now work). **It unblocked two dossiers**, both
moved to Ready above: `2026-08-19-content-area-spacing-and-scroll-contract` and
`2026-08-21-visual-refinement-phase1-token-adoption`. No migration, no API change; frontend
only. **Five things a later reader needs.**

**D-10 is the one to read before touching `.app-shell`'s sizing, and it nearly shipped.**
The approved §7 said `flex: 1`, which expands to a `0%` basis — and `.app-root` carries only
`min-height`, so its inner size is indefinite and a percentage basis resolves to `content`.
The shell was sized by `main`'s content, grew past the viewport, and handed scrolling to the
**document**: measured 3805px shell, 3385px of document scroll. That is the exact inversion
of `02-layout-shell.md` §3.3 and the ground the *next* dossier's whole scroll contract stands
on. `flex: 1 1 0px` — a definite length — fixes it. **No unit test in this repository can see
this**, and a short seeded list hid it; what surfaced it was an e2e precondition assertion
failing instead of passing vacuously.

**The browser pass happened and was measured** (D-12, D-13): five layout assertions green
against a live compose stack, both central fixes mutation-probed in the browser (reverting
the sticky wrapper puts the header at y=-154; reverting the shell basis gives 3593px of
document scroll), `SEmptyState` centred at 244/244 in a 600px column, and Q-5's accepted cost
quantified — a wide sticky table gives `main` 1868px of horizontal scroll and the document 0.

**D-13 corrects the dossier rather than the code.** F-41's §2 wording ("the title is clipped
above y = 0") overstates it at the 844x390 the dossier names: the panel overshoots by ~4px
there, so what the old rule put out of reach was the panel's top edge and part of the header.
The defect and the fix are real; the magnitude is small and grows as the viewport shortens.

**D-11 is a testing lesson worth carrying.** A sticky header only pins while its own table is
still crossing the scrollport, so filler placed *after* a table releases it before it can be
measured — and Chromium constrains a sticky child against the scrollport's **content** box,
so the header pins one content gutter below the top bar (80px = 56 + 24), not flush at 56.

**FU-9 is an operational trap that will cost the next person an hour.** The test compose
stack cannot bootstrap Vault as shipped: `smap.bootstrap vault-init` reads
`/deploy/vault/policies/*.hcl`, which `compose.test.yml` never mounts. Until it is bootstrapped
every login 500s on `InvalidPath: transit/keys/smap-jwt-sign` while `/readyz`'s vault probe
(connectivity only) reports healthy — so the stack looks up and is not. The working `docker cp`
recipe is in the dossier's FU-9; adding the bind mount is the real fix. Also note the full
suite needs `backend-worker` and the two knowledge workers, or exports and jobs never settle
(D-15).
Removed on 2026-08-21 after implementation:
`2026-08-19-chatroom-scroll-and-composer` (the message feed now holds the reader's position
through a history load, counts only what actually arrived, re-pins after content grows late,
and orders approval cards by when their gate was raised). Nothing lists it in `depends_on`, so
no row moved out of Blocked. No migration; frontend only, six files in
`slices/conversation` plus the header. **Six things a later reader needs.**

**The browser pass happened, and it was measured rather than eyeballed** (D-7). Full compose
stack up locally, a temporary Playwright harness against it, and five of the six browser
criteria closed with numbers: anchor drift `[0,0]` px across two history loads (the pre-fix
expression gives `[240,240]`, probed against the running app); auto-pagination
`[101,201,260,260,...]` on wheel-to-top, settling rather than looping; composer 37px -> 121px
with no internal scroll -> capped at 192 with `scrollHeight 268`; empty state centred to `0`px;
and a message carrying Mermaid + KaTeX + highlight landing 24px above the fold with the feed
still pinned. That breaks the streak of dossiers in this area closing unobserved.

**AC-9 is deliberately unticked** (D-5, FU-7), and the reason generalises: `fake_provider.py`
answers only key-upload probes, so **the test stack cannot produce a streamed agent reply at
all**. Nothing in `frontend/e2e/` drives `agent.token`, which means the streaming bubble, the
tool-round reset and the turn watchdog have never been seen end to end by anything.

**D-2 is the one to read before touching the socket.** The dossier named three places that
must flush the token buffer before clearing a stream draft. There are **six**, and the one it
would have missed - `agent.progress{tool_round}` - is the one whose omission is visible: the
superseded round's tail reappears on top of the new round, which is the exact flash that clear
exists to prevent. All six now route through one `resetAgentStream()` so a seventh inherits the
rule instead of having to remember it.

**Q-7 was incomplete and the gap was invisible from the spec** (D-3). It said the compact
band's overlay panels open from "header toggles, reusing the existing refs" - but the buttons
that set those refs are `v-if="isMobile"` and `v-if="!isDesktop"`, and 1024-1279 is neither, so
the panels would have shipped unreachable. If you add a band, check who can still reach the
controls in it.

**Three defects in this task's own diff were caught by its gates, not by review**, all of the
same family: two watches keyed on feed *length* where identity matters (a `pending-<uuid>` key
swap changes the list without changing its count), and a `pre`-flush watcher that could root an
IntersectionObserver on the viewport instead of the feed. Plus a self-audit catch: the prepend
capture disarms the pill and the auto-loader, and only the restore rearms them, so a throw
between the two bricked both for the session - now restored in a `finally`.

**F-29's second arm was cut at approval** (Q-8, FU-6): the agent rail still renders from 768px
where the responsive spec says drawer. It is the only item in the dossier whose correction
*removes* a surface users have today, so it wants its own reviewable change. T-9 asserts at
800px that the rail is still there, so the deferral is pinned rather than merely intended.
Removed on 2026-08-21 after implementation:
`2026-08-20-onboarding-without-smtp` (an install with no outbound mail can now onboard:
every invite create returns a copyable accept link, a project invite is picked from the
parent Org's members, and an Admin can provision an account and hand over two activation
links). Nothing lists it in `depends_on`, so no row moved out of Blocked. It **applied an
SRS Delta** at approval, rewriting [R6.09] and adding [R6.18]. No migration; every change
is additive, so reverting the frontend alone leaves a working system. **Five things a
later reader needs.**

**D-16 is the entry to read before touching account state anywhere.** A post-close
`/code-review` found that the "already activated" guard tested `password_hash is not None
and email_verified` — and a Google-provisioned account has `password_hash=None` with
`status=ACTIVE, email_verified=True` (R6.15/R6.16), so for every Google user the guard
never fired and an Admin could mint a working set-password link for a fully live account.
**"Has a password" is not a synonym for "can log in" in this codebase**; the test is a
verified address plus *any* usable credential, password or linked identity, which is what
`LastCredentialError` already encodes. D-21 carries the same lesson onto the client: the
re-issue button is shown for every live account and the 409 is the answer, because gating
it on `email_verified` would hide it from a provisioned user who walked the verify link
first and still needs their password link.

**D-12 is a hole the security gate found in the dossier's own reasoning.** Q-6 and §8 both
claimed the invitable-member pool "discloses nothing new" because it is a subset of
`GET /api/orgs/{id}/members`. Capability #14 does **not** establish org membership — a
user invited straight into an org-owned project as its Owner holds #14 and appears in no
`org_members` row — so the endpoint as first written would have handed them every address
in the parent Org. The caller's own org membership is now a predicate of the query, and a
caller outside the Org gets an empty pool, indistinguishable from a user-owned project.

**D-18 is a live defect this task fixed on the way, and its shape recurs.**
`ProjectMembersView` decided "may I invite" from its own row in the member list. Project
ownership is inherited, so an Org Owner manages the project holding no `project_members`
row, and the invite card was hidden from exactly the people the picker was designed for.
Same fix as `2026-08-20-orchestration-room-scoped-reads` D-7: read `ProjectOut.is_moderator`,
never the member list.

**The browser pass happened** (D-23), which breaks a seven-dossier streak in this area.
Compose stack up locally, `e2e/20-onboarding-without-smtp.spec.ts` green against it, all
four new surfaces observed, and four mutation probes each turned the intended test red.
One operational trap it surfaced: the backend primes its rate-limit policies at boot, so a
backend started before `alembic upgrade head` leaves that table missing and every later
login bounces — restart it after migrating.

**D-24 is a post-close `/code-review` catch that outlives this task.** `useFocusTrap`'s
watcher had no `immediate`, so a dialog mounted **already open** — `v-if="result"` on the
wrapper with a constant `:open="true"` inside — never fired it: no focus move, no scroll
lock, Tab walking the page behind the modal. Fixed in the shared composable rather than at
the call sites, and it now has the regression test it never had. If you add a dialog that
can mount open, this is why it works.

**FU-11 is a standing warning, not this task's bug.** The email-domain allowlist that
`docs/operations.md` §7a.5 now tells operators to depend on lives in Redis under
`allkeys-lru` with no TTL, and an absent `mode` reads as `off` — so memory pressure or a
restored Redis silently reopens registration. And **no API writes those keys** (D-2, FU-6):
Q-4 declined an invite-only mode because the policy is "admin-tunable at runtime", which is
true only via `redis-cli`.
Removed on 2026-08-21 after implementation:
`2026-08-20-orchestration-room-scoped-reads` (an orchestration record naming a chat room is
now readable by exactly that room's readers, one naming none is backstage under [R14.10],
and the workflow trace is Admin + Owners again). Nothing lists it in `depends_on`, so no row
moved out of Blocked. It **applied an SRS Delta** at approval, strengthening [R14.10] and
adding [R15.24]. No migration. **Five things a later reader needs.**

**The db tier ran, and the mutation probe is why it means anything.** One
`pgvector/pgvector:0.8.0-pg16` container on 5433 (the recipe in the member-groups dossier's
D-8 still works) plus `alembic upgrade head`; replacing the room predicate with an
unconditional allow killed 11 tests across the unit and db tiers, and slicing before
filtering killed the pagination-disclosure test. Both reverted.

**D-5 is a live defect the AC-8 work uncovered, and its siblings are still out there.**
`useChatroomSocket` tested `instanceof ApiError` against the *generated client's* class
while the transport throws `@shared/errors`' — so the 404 branch was dead and an approval
whose row was gone pinned a `pending` card until reload. Two more sites do the same thing
(FU-5: `useChatroomMessages.ts`, `usePromptAssistantSocket.ts`), their tests construct the
same wrong class, and the durable fix is a lint rule rather than three edits.

**AC-8 was verified by substitution with the user's agreement (D-2)**: the approval read is
driven against a real PostgreSQL as a real ordinary room member, and the card's reconcile is
driven through all three branches in a unit test — but **no browser and no Playwright**, so
`ApprovalCard`'s rendering and the vote action are still reasoned about rather than seen.

**The list routes deliberately have no project-level precondition** (D-3, decided with the
user): every row is gated individually, so a caller with no role gets `200 []` rather than
403. Do not re-add a membership pre-check "for safety" — that is the weaker gate AC-9 exists
to keep out, and the existence oracle is unchanged either way.

**`check:openapi-drift` still cannot run on this host** (D-4, same as the member-groups
dossier): the bash script cannot find `python`. The check was done by hand — regenerating
`openapi.json` and the client both produced zero diff, because this task changed module
docstrings and gate internals, not route docstrings or response models. **That stopped
being true at D-7**, which added `ProjectOut.is_moderator`; the spec and client were
regenerated for it.

**D-7 is the one to read before touching project authorization anywhere.** A post-close
`/code-review` found that the new workflow guard locked out Org Owners: the client decided
ownership from the `project_members` list, and ownership is *inherited*, so an owner of the
parent org holds no such row while every server gate treats them as a project owner. Fixed
by serializing the verdict (`ProjectOut.is_moderator`) from
`TenancyRoleResolver.moderated_project_ids`, the batch form of the gates' own predicate,
placed beside `roles_for` so the two cannot drift. **If you need "is this caller an owner"
on the client, read that bit — do not read the member list.** The same fix corrected the
agents, agent-groups and conversation Concept Map owner panels, which had the bug too.
Removed on 2026-08-20 after implementation:
`2026-08-20-member-groups-and-room-visibility-isolation` (the three listing endpoints now
filter through the room ACL, and a project can define optional Member Groups that scope a
chat room below project level). Nothing lists it in `depends_on`, so no row moved out of
Blocked. It **applied an SRS Delta** at approval, adding [R5.06], rewriting 13.2 and
[R13.04] for five flags plus a server-refused mutual exclusion, and adding 13.2a
[R13.28]-[R13.32]. Migration 0079. **Four things a later reader needs.**

**D-9 is a real hole this task opened and the security gate caught**, and the shape of the
fix is the lesson: `OrgService.remove_member` deletes `project_members` rows at the
repository layer and knows nothing about groups, so a user removed from an org kept reading
a room bound to a group they were in. It is closed at the ACL — `group_ids_for_user` joins
to `project_members` — rather than by adding a cleanup call to the one path that was
missing it. If you add another way to end a project membership, you do not need to remember
anything.

**Both mutation-probed.** Every claim in this dossier that a filter closes something was
checked by breaking the filter and watching a test go red: the flag predicate, the
workspace soft-delete join, and D-9's membership join.

**AC-12 is deliberately unticked** (D-12, FU-13): the revocation mechanism is proven
against a real database, but nothing drives a live WebSocket across a group removal.

**The db tier now runs on a developer host**, which BOARD.md previously recorded as
impossible here — one `pgvector/pgvector:0.8.0-pg16` container on port 5433 plus two env
vars, recipe in the dossier's D-8. Migrations 0077, 0078 and 0079 have all now been applied
somewhere, which retires the "never applied anywhere" note on the two earlier dossiers.
`check:openapi-drift` still cannot run here (D-14).
Removed on 2026-08-18 after implementation: `2026-08-18-agent-delegated-activity-control`
(a room creator can delegate activity start/end to one bound agent, per room, scoped to an
allowlist of activity types, exercised only through a structured tool call). Nothing lists
it in `depends_on`, so no row moved out of Blocked. It **applied an SRS Delta** at approval,
amending [R30.21], [R30.22] and [R30.35] and adding [R30.37]. **Four things a later reader
needs.** **D-6 is a real defect in the approved spec, found by the security gate and not by
review**: §5 justified naming the initiating agent to the whole room with "an agent bound to
a room is already named on every message it sends", which is true of a `normal` agent and
**false of an observer** — one sends no messages and is filtered out of every non-creator's
roster ([R28.10]) — so the broadcast would have been the single channel that outs a granted
observer to the class. Both attribution fields are now withheld for an observer-started
round. If you extend this attribution anywhere, carry that rule with it. **FU-9**: the
`db`/`integration` tier has never run for this task and **migration 0078 has never been
applied anywhere** (no Docker, no local PostgreSQL) — AC-3 is left **unticked** rather than
claimed, and the 0078 atomicity tests plus the CHECK-constraint tests are CI's to close.
**D-5**: AC-19's manual browser pass was converted, with the user's agreement, into
`frontend/e2e/18-delegated-activity-control.spec.ts`; it covers the grant lifecycle end to
end but deliberately does **not** drive an agent calling the tool (a live provider key and a
model that chooses to call it — §10 R-2's untestable half), and **it has not been executed**,
so AC-19 is unticked too. That is now seven consecutive dossiers in this area with no
behavioural verification. And the trade worth not undoing: **the platform imposes no pacing
on a delegated agent at all** (Q-2) — no cooldown, no rate limit, no refusal to end a round
while participants are still working. The shipped TA runs at `every_n_messages: n=1`, so
that is not hypothetical; the dry-run checklist in `docs/examples/creative-thinking-course.md`
now carries the two items that probe it, including the prompt-injection probe, because the
prompt is the only thing standing in front of it.
Removed on 2026-08-17 after implementation: `2026-08-17-activity-participant-lifecycle` (the
participant's self-serve session start/finish is gone from the chatroom Activity rail, an
`ActivitySession` belongs to the `ActivityActivation` it was answered under, ending a round
closes its sessions, and a reversible "I'm done" signal feeds a facilitator-only
completed/in-progress count). Nothing lists it in `depends_on`, so no row moved out of Blocked.
It **applied an SRS Delta** amending [R30.01] and [R30.22]. **Three things a later reader
needs.** **Migration 0077 has never been applied anywhere** (D-7): no Docker and no local
PostgreSQL, so the `integration`/`db`/`wiring` tiers never ran, and **AC-3 and AC-9 are left
unticked rather than claimed** — the tests exist and are CI's to close. No browser check either,
so every user-facing change here has been reasoned to work and not seen to; that is now six
consecutive dossiers in this area with the same gap. **D-3** is a defect in the approved spec
worth learning from: seeding the participant's done-toggle from the PATCH response alone loses
it on every reload, because the client holds no session id — a symmetric GET was added.
And **FU-7**, which is a release constraint rather than a nice-to-have: 0077 deliberately does
**not** drop `uq_activity_sessions_open`, because pre-0077 `create_open` relies on it and
dropping it would let the upgrade window produce the very duplicate sessions 0077 exists to
prevent (D-12). Dropping it is a separate migration that **must not ship in the same release**.
A post-implementation `/code-review` found that plus two live defects — a submission moved the
facilitator's counts without publishing them, and the participant's done-toggle did not follow
the server after answering again — both fixed with tests (D-10, D-11).
Removed on 2026-08-17 after implementation: `2026-08-16-platform-type-delete-optin-lifecycle`
(an admin platform-type delete now removes every project's opt-in explicitly and records the
count on the `activity_type.deleted` event, instead of relying on an FK cascade that a soft
delete can never fire). Nothing lists it in `depends_on`, so no row moved out of Blocked.
**Three things a later reader needs.** **The db tier is the only thing that found the real
defects, and it found two.** No Docker and no local PostgreSQL on the implementing host, so AC-1
shipped unverified (D-4) and was answered by CI: the first run failed, not on an assertion but on
**fixture teardown** — `audit_logs` has an `ON DELETE SET NULL` FK to `users` and an append-only
trigger that refuses the UPDATE that cascade performs, so dropping the `project` fixture's
throwaway user breaks for any test that emitted an audit event as it. This was the first `db`
test to do so; `tests/integration/conftest.py` now clears those rows under
`SET ROLE smap_audit_retention` (D-9), which is a landmine removed for everyone. The same push
also failed `frontend-gate-openapi-drift` twice: once because a FastAPI route **docstring** is
published as the operation description and `make openapi-types` was never run (D-8), and again
because the regenerated spec was committed with a **UTF-8 BOM** that PowerShell redirection adds
and `core.autocrlf` does not normalise (D-10). AC-1 is now green on run `31993358787`. D-5 still
stands: no behavioural verification in a browser.
**D-3** — the db test does more than the spec asked, because `check-quality` found that nothing
in the change exercised the real cursor: the unit tier mocks `result.scalars().all()`, so a
misread `RETURNING` clause would delete correctly, pass everything, and write
`optins_removed: "0"` forever; the test now reads the audit row back. And **D-7**, which outlives
this task: `tests/unit/test_graphrag_builder.py` **hangs indefinitely on this host**, in
isolation as well as in the tier, so 48 tests are silently unrunnable locally and only CI covers
them. Unrelated to this diff (last changed in `1c3bad6`), but somebody should chase it.
Removed on 2026-08-17 after implementation: `2026-08-16-shared-common-i18n-namespace` (the
`common.*` namespace now exists in both shared bundles, so all seventeen call sites resolve
instead of rendering their English default arguments). Nothing lists it in `depends_on`, so no
row moved out of Blocked. **Two things a later reader needs.** **FU-4** — the reason no test
caught this and the reason the next one will not either: `renderView` mounts the shared i18n
singleton with **no** bundle loaded at all, so all 182 component test files assert raw keys or
English defaults. A key deleted from `zh-TW.json` only is invisible to the entire suite the same
way; the cheap fix is a per-slice bundle-parity test, not a harness rewrite. And **D-2** — the
recurrence guard scans `src/` for `t('common.X')` and asserts every hit resolves, but it excludes
`__tests__/` and asserts the scan finds at least one call site, so a glob that silently stops
matching fails loudly rather than passing vacuously.
Removed on 2026-08-17 after implementation: `2026-08-16-mandala-center-fallback` (the mandala
grid resolves `center` as a named opt-in, so a nine-field schema declaring none renders in its
declared order instead of having its first field promoted to the middle). Nothing lists it in
`depends_on`, so no row moved out of Blocked. **Two things a later reader needs.** **D-1** — a
schema naming no centre now renders with *no* highlighted cell, because `isCenter` returns false
for every field once `centerField` is null; that is correct but was not stated in the spec, so
the test asserts the absence rather than leaving it implied. And **D-3** — no behavioural
verification (Docker unavailable), though the exposure is narrower than usual: the shipped course
declares `center`, so no shipped type changed and only projects reusing the `mandala-9grid` key
with their own centre-less schema see any difference.
Removed on 2026-08-17 after implementation: `2026-08-16-example-pack-prompt-grounding` (the AA
prompt no longer asks who has not submitted, states that its activity block is a bounded recent
window whose gaps are not evidence, and refuses coverage questions back to the teacher). Nothing
lists it in `depends_on`, so no row moved out of Blocked. **Three things a later reader needs.**
**D-1** — the prompt says 數十筆 rather than naming 30, because a literal number would be a second
uncoupled copy of `DEFAULT_ACTIVITY_WINDOW` that would silently start lying if the constant
moved; the figure lives in the walkthrough next to the constant instead, and **FU-5** records
that nothing ties the two. **D-3** — AC-8's dry-run checklist did not exist, so it was created
covering all five behavioural checks rather than adding one free-floating item to nothing. And
the operational point that outlives the diff: **the fix does not reach an installed deployment**
— pack agents are copied on import and install is idempotent by name, so any project that
already installed `creative-thinking-room` still holds an AA carrying the old prompt and must
edit or re-create that one agent by hand. Observations the old prompt already produced are not
retracted.
Removed on 2026-08-16 after implementation: `2026-08-16-example-dialog-pending-and-optout`
(both example surfaces now gate every action button on "is anything in flight" read off the
mutations themselves, and the hand-maintained `pendingId`/`installingKey` refs are gone along
with the `onSettled` clears that released the wrong request's lock). Nothing lists it in
`depends_on`, so no row moved out of Blocked. **Three things a later reader needs.** **D-1** —
the fix went further than §7.1 asked: *both* questions are now answered from vue-query, "is
anything pending" from `isPending` and "which row" from the mutation's own `variables`, which
deletes the second half of the root cause instead of patching it. That leaves
`AgentPackInstallDialog` as the last site still on the D-14 hand-maintained form, and **FU-5**
records the shared `@shared/composables` helper the three sites should collapse into. **D-2** —
the buttons gained a `:loading` spinner they never had: gating on "anything pending" disables
every button and destroys the only signal that a click registered, so the spinner replaces it.
And **D-6** — no behavioural verification, again (Docker unavailable); two user-visible changes
are unobserved, so confirm on the first deployed build. That is the fifth consecutive dossier
in this series to record the same gap.
Removed on 2026-08-16 after implementation: `2026-08-16-example-docs-corrections` (the
walkthrough now states the `filled_count` boolean rule the code actually implements and points
at `_is_filled`'s docstring as the authority, and a new Limitations entry says that the install
fallback's provider substitution voids the packs' shipped temperatures on OpenAI). Nothing
lists it in `depends_on`, so no row moved out of Blocked. **Two things a later reader needs.**
**D-4** — AC-7's em-dash rule was applied to the **whole** document, not only the two sections
this dossier owns: 23 occurrences, roughly 20 of them inside sections that
`example-pack-prompt-grounding` and `platform-type-delete-optin-lifecycle` own. Punctuation
only, no claim changed, but those dossiers will hit conflicts on lines they expected to merge
cleanly and must rebase. And **D-2** — AC-3 asked the entry to say "Claude and Gemini forward
temperature", which would have been a second per-provider claim of exactly the kind this
dossier exists to fix; the rule is per *resolved model*, and `claude-*-5` / `claude-opus-4-[7-9]`
reject sampling too, so the entry says that instead.
Removed on 2026-08-16 after implementation: `2026-08-16-migration-0076-retry-safety` (0076 is a
single transaction in both directions, the three stale copies of the `transactional_ddl` rule
are corrected, and a structural test pins the no-statement-before-an-autocommit-block rule
across all 80 migrations). Nothing lists it in `depends_on`, so no row moved out of Blocked.
**Two things a later reader needs.** The dossier was parked because AC-1/AC-2 could not be
measured; they are now measured, and the reason they were not is worth knowing. **D-7** — the
db-tier atomicity tests gate on `SMAP_SCRATCH_DATABASE_URL` and **nothing ever set it**, so they
had never executed anywhere while `backend-db` reported `68 passed, 5 skipped` and read as full
coverage. `ci.yml` now creates a `smap_scratch` database on the postgres service that job
already starts; the tier is at `70 passed, 3 skipped`. If that step is ever removed these go
quiet rather than red. **D-8** — the first run that actually executed them failed in both
directions on the *tests*, not the migration (SQLAlchemy 2.0 autobegins on the first `execute`,
so the pre-check assertion owned the transaction the migration needed). And the one thing still
outstanding, deliberately routed to FU-3 rather than held against the dossier: **production's
`alembic current` is still unread**, and prod has no automatic migration step at all (FU-5).
Removed on 2026-08-16 after implementation:
`2026-08-16-admin-platform-type-edit-unreachable` (the shipped-examples section resolves its
edit target from a new unbounded platform-only listing instead of one 200-row page of the
cross-project one, so an installed example can always be edited; the cards show stored values
rather than the course file's). Nothing lists it in `depends_on`, so no row moved out of
Blocked. **Three things a later reader needs.** **D-4** — §7.3's truncation warning was
*replaced*, not implemented: Q-1's unbounded route leaves no page limit to key one on, and
`admin.activities.truncated`'s "Showing the most recent {count}" could not be true of it, so the
section warns on an unresolved row instead, under a new key. **D-5** — §5's account of the
reseed defect names a trigger that cannot happen: vue-query's structural sharing returns the
*previous* object for a deeply-equal refetch, so an identical refetch never reaches the watcher
at all and the literal §8.2 test passed against the pre-fix code. The live case is a refetch
whose **contents** changed; both are now tests. This sharpens FU-4's sweep for
`watch(() => [`. And **D-6** — no behavioural verification, again (Docker unavailable); four
user-visible behaviours changed and none has been seen in a browser, so confirm on the first
deployed build — **D-9** sharpens that: a post-close `/code-review` found two more windows in
exactly the residual-state handling jsdom was asserting (the warning fired after a *successful*
install, and a row deleted by another admin blanked an open form), both now fixed with tests.
**File overlap** with the still-open `example-dialog-pending-and-optout` in
`ActivityExamplesSection.vue`: that dossier owns the `installingKey` pending state, this one
rewrote row resolution, the card rendering and the Edit button's guard around it. Rebase.
Removed on 2026-08-16 after implementation: `2026-08-16-activities-install-error-contract` (an
unknown admin `course_key` is now a mapped 404 carrying the shipped-course list instead of a
logged 500, and `_validate_validator_config` finally receives the `payload_schema` it must
score `min_filled` against, so `register`/`update` refuse an unpassable threshold with the same
422 every other validator-config refusal produces). Nothing lists it in `depends_on`, so no row
moved out of Blocked. **Three things a later reader needs.** **D-1** — it also closed the three
pre-existing `__all__` omissions in `activities/domain/errors.py`, which retires FU-3 of
`2026-08-09-platform-example-activity-types`. **D-2** — `pytest -q` was NOT run to completion:
the `integration`/`wiring`/`db` tiers need a live PostgreSQL and Docker was unavailable, so
they fail at connect (`getaddrinfo failed`); the `unit` tier, which holds every test this
dossier touches, is green. And the deliberate non-goal worth not undoing: a course file that
exists but does **not parse** still produces a 500, because that is a defect in the deployed
artifact and reporting it as "not found" sends an operator to the wrong place — a negative test
pins it.
Removed on 2026-08-16 after implementation:
`2026-08-16-activity-type-key-collision-across-scopes` (both doors onto a cross-scope key
collision now warn without refusing, the facilitator picker and the type list distinguish the
two rows by `scope`, and the activity signal carries `activity_type_id`/`activity_type_scope` so
a rule written from now on can pin one). Nothing lists it in `depends_on`, so no row moved out
of Blocked. **Four things a later reader needs.** This dossier **applied an SRS Delta** amending
[R30.02] (`REQUIREMENTS.md:2161`): key uniqueness is per scope, the collision is permitted, and
`scope` is the disambiguator — so a future dual-scope entity has a stated rule to follow.
**D-3** — the `opt_in` warning reports *state, not this call's effect*: a repeat opt-in that
inserts nothing still reports the collision it left behind, matching what
`smap/examples/_seeding.py` already does, and the field name `shadowed_by_platform` is
deliberately shared with the seeder's report. **D-8** — no behavioural verification at all
(Docker unavailable); four user-visible surfaces changed and none has been seen in a browser,
so confirm on the first deployed build. And the central trade, stated plainly: **the collision
is not prevented**, so an *already-stored* workflow rule naming only `activity_type_key` still
matches both types. The new optional `activity_type_scope` filter helps only rules written
after this change; FU-1 records the report that would tell an operator which existing rules to
edit by hand.
Removed on 2026-08-16 after implementation: `2026-08-16-activity-submission-wakeup-gap` (an
activity submission now re-arms the per-agent silence clock through a new
`triggers.evaluate_room_activity`, so an agent on `silence_minutes` no longer reads a class
filling in a worksheet as a lull). Nothing lists it in `depends_on`, so no row moved out of
Blocked. **Two things a later reader needs.** **D-1** — Q-3 justified importing the conversation
*application* layer into the route on a false premise: `activities.py` imports only from
`interfaces` and was clean under the route rule, so the fix goes through
`ConversationFacade.note_room_activity` instead. Behaviour and call site are exactly as approved.
And the deliberate non-goal worth not undoing: a submission re-arms the clock but is **not**
counted by `every_n_messages`, because the shipped teacher agent runs at `n=1` and counting would
mean one agent turn per student per submission. A negative test pins it.
Removed on 2026-08-16 after implementation: `2026-08-16-agent-pack-install-report-fidelity`
(the pack install report now carries `group_created`, and the dialog renders each agent's
preferred provider and bound activity types, reports the provider actually used, and states
that a design agent's drafts are copied by hand). Nothing lists it in `depends_on`, so no row
moved out of Blocked. **Two things a later reader needs.** **D-1** — no behavioural
verification was performed: Docker was unavailable, so the install flow was never exercised in
a browser and the `integration`/`db`/`wiring` test tiers are unrun locally; this dialog has now
shipped twice without a manual pass (the source dossier's D-12 was the first), so confirm on
the first deployed build. And **D-4** — this task's uncommitted work was stashed mid-build by a
concurrent session on the same branch; it was recovered intact, but the task base moved from
`bf1edcb` to `9bec23a` and both audit gates were run against the later base.
Removed on 2026-08-16 after implementation: `2026-08-16-example-cli-seeder-scope-leak` (the
example CLI seeder now keys idempotency on ownership via a new `list_owned_by_project` read
rather than on `list_types`' usable set, and warns per key that shadows an opted-in platform
type). Nothing lists it in `depends_on`, so no row moved out of Blocked. **One thing a later
reader needs:** its Q-2 warning and `2026-08-16-activity-type-key-collision-across-scopes`
(F-5) describe the same collision from two sides; when F-5 is built, its warning wording should
be reconciled with the seeder's rather than duplicated.
Removed on 2026-08-14 after implementation: `2026-08-13-creative-thinking-example-agents`
(two shipped agent packs installed copy-on-import into a project, the creative-thinking course
transcribed from its actual worksheets, and an explicit `x-order` on payload-schema
properties). Nothing lists it in `depends_on`, so no row moved out of Blocked. **Two caveats a
later reader needs.** AC-4's `db`-tier test — `tests/integration/test_activity_schema_key_order.py`,
which pins that `jsonb` really does discard payload-schema key order — has never been
executed: Docker was unavailable on the implementing host, so the entire `x-order` half rests
on reasoning until CI runs it, and §10 says what to do if it fails. And **D-12**: no
behavioural verification was performed at all, for the same reason; confirm the install flow
and the corrected worksheets on the first deployed build.
Removed on 2026-08-09 after implementation: `2026-08-09-platform-example-activity-types`
(migration 0076 gives `activity_types` a `scope`, the example catalogue moves out of the
`smap` CLI package into `contexts/activities/infrastructure/examples/`, and seven duplicated
tenancy checks collapse into one reachability rule gated on a per-project opt-in). Nothing
lists it in `depends_on`, so no row moved out of Blocked. Two things a later reader will
want: **D-1** — a platform-scoped type must declare an `in_process` validator, because mcp
and webhook validators have no project to run in; **FU-8/FU-9** — the catalogue and opt-out
queries are unbounded, bounded today only by how many examples an admin installs.
Removed on 2026-08-09 after implementation: `2026-08-09-chatroom-rail-scroll-and-resize`
(opt-in `fill` on `STabs`, the missing `min-height`/overflow on `.chatroom__presence`,
`ActivityPanel`'s own scroll region, a resizable persisted rail width, and
container-relative layout for activity plugins). Nothing lists it in `depends_on`, so no
row moved out of Blocked. **Carries an unusual caveat for a closed dossier:** AC-1, AC-3,
AC-5 and AC-12 are layout outcomes that jsdom cannot assert, and the dossier was closed
without the manual browser check (D-5). The reported symptom has been reasoned to be fixed
from the CSS, not observed fixed in a browser. Confirm on the first deployed build.
Removed on 2026-08-09 after implementation: `2026-08-08-activity-example-catalogue`
(`smap/examples/` is now `courses/*.json` + a validating `_catalogue.py` loader + a
course-agnostic `_seeding.py`; `creative_thinking_course.py` deleted, course JSON shipped as
package data). Nothing lists it in `depends_on`, so no row moved out of Blocked. It does
retire FU-5 of `2026-08-08-creative-thinking-course-example` as a code task: seeding the
other six units is now one JSON file and no Python, pending the collaborating educator's
confirmation of the unit designs (carried forward as this dossier's FU-1).
Removed on 2026-08-01 after implementation: `2026-07-22-wait-for-event-timer-and-join-ports`
(timer waits now arm their own `delay_seconds` via `workflow_event_resume`; the join
`timeout` port's absence is recorded via linter advisory + docs rather than built, per Q-2).
Nothing lists it in `depends_on`, so no row moved out of Blocked.
Removed on 2026-08-01 after implementation: `2026-07-22-turn-outcome-reporting` (C2 and C3's
frontend-only slices on 2026-07-31, then C1, C4 and C3's backend half once
`turn-idempotency-and-locking` released `turn_engine.py`). Nothing lists it in `depends_on`, so no
row moved out of Blocked. Two things it leaves behind that a later reader will want: **FU-10** —
`_post_commit` catches `Exception`, so a *cancellation* in the post-commit window still rewrites a
committed turn as failed; the fix belongs with `_finalize_failed_turn`, which
`turn-idempotency-and-locking` owns. **FU-11** — `agent.progress` beacons cover the gaps between
assembly steps, not a single provider call that outlasts the 120s watchdog. It also closes
`chatroom-socket-lifecycle`'s FU-8, which had been waiting on this dossier's C3.
Removed on 2026-08-01 after implementation: `2026-07-22-workflow-capability-enforcement`
(can_approve/can_instruct gated at runtime, advisory linter + picker markers, max_alive_subagents
bounds, migration 0073 applied and downgrade-checked). Nothing lists it in `depends_on`, so no
row moved out of Blocked.
Removed on 2026-07-31 after implementation: `2026-07-22-turn-idempotency-and-locking` (all six
commits C1–C6, migration 0072 applied and downgrade-checked). Nothing lists it in `depends_on`, so
no row moved out of Blocked. It does unblock `2026-07-22-turn-outcome-reporting`'s backend half:
that dossier's D-1 deferred C1/C3/C4 because `turn_engine.py` was being rebuilt here, and that
rebuild is now committed. Re-verify its citations before resuming — this work restructured
`run_turn` (the lock loop is wrapped in a `try/finally` that drains the coalesced trigger), split
`_run_locked`'s failure handling into a shared `_finalize_failed_turn` with a third `except` arm
for a lost lock, and changed `distributed_lock` to yield a `LockHandle` instead of a bool.

Removed on 2026-07-28 because their own frontmatter reads `implemented` and the board only
lists unfinished work: `2026-07-22-activity-session-authz-and-validation`,
`2026-07-22-workflow-run-cancellation`, `2026-07-28-activity-schema-participant-access`.
Also removed on 2026-07-29 after implementation: `2026-07-22-reingest-allowlist-propagation`,
`2026-07-29-knowledge-ingest-concurrency-and-enqueue`,
`2026-07-29-knowledge-upload-resource-bounds`, `2026-07-29-knowledge-ingest-ports`,
`2026-07-29-knowledge-document-ui-split`, and `2026-07-22-retention-sweep-fixes`.
Removed on 2026-07-29 after implementation: `2026-07-22-search-determinism-and-highlighting`.
Removed on 2026-07-30 after implementation: `2026-07-22-settings-form-reconciliation`. Nothing
listed it in `depends_on`, so no row moved out of Blocked.
Removed on 2026-07-31 after implementation: `2026-07-22-tool-dispatch-failure-categories`.
Nothing lists it in `depends_on`, so no row moved out of Blocked. It does change the ground
under `2026-07-22-turn-idempotency-and-locking`, which names it as a textual adjacency: this
work restructured `_stream_with_tools` (the tool-round loop is now a bounded `for` over
attempts with its own round counter, and the function returns `ToolLoopOutcome` instead of
`tuple[str, int]`), so that dossier's citations into the turn loop need re-verifying before
it starts.
Removed on 2026-07-30 after implementation: `2026-07-22-subagent-spawn-fail-fast`. Nothing listed
it in `depends_on`, so no row moved out of Blocked. It does validate two standing assumptions in
`2026-07-22-workflow-capability-enforcement`: `SubagentService.spawn` now has **zero** production
callers, so that dossier's Q-2 (no runtime gate for `can_create_subagent`) and its R6 (zero file
overlap) both hold as written.
