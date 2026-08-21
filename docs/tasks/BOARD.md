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

- (moved to In progress on 2026-08-21) `2026-08-19-chatroom-scroll-and-composer`. The original
  entry, kept here for the record:
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
- `2026-08-19-shared-overlay-and-shell-defects` (bugfix, draft) - unblocked by the completed
  `2026-08-19-transient-feedback-channels`. Fixes the shared overlay primitives: `STable`'s
  sticky header is inert, `SDropdown` has no flip or height cap (and measures before the menu
  exists), `ErrorBoundary` wraps the whole layout so a render error blanks the shell, and the
  404 route has no `meta`. Its impersonation-banner z-index decision must retain the completed
  predecessor's toaster contract at `--z-toast: 500`.

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

## Blocked

From the 2026-08-19 page-presentation audit. Every entry below is blocked only by file
overlap, so each unblocks as soon as its predecessor is `implemented`.

### From the 2026-08-21 visual-refinement analysis

Two dossiers, sequenced. Not from an audit: they came from a direct read of `main.css`, the
46 `shared/ui/` components and the two `docs/UI/` specification files, prompted by the user's
report that the UI is consistent but flat.

- `2026-08-21-visual-refinement-phase1-token-adoption` (refactor, **approved 2026-08-21**) - waiting on
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
- `2026-08-21-visual-refinement-phase2-identity-and-depth` (feature, **approved 2026-08-21**) -
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

- `2026-08-19-content-area-spacing-and-scroll-contract` (bugfix, draft) - waiting on
  `2026-08-19-shared-overlay-and-shell-defects`. Both edit `AppShell.vue`, `router.ts` and
  `AgentDetailView.vue`. Strips the duplicated padding from 34 view roots (and the nested
  `<main>` from 23 of them), and gives navigation a scroll-reset contract, which today does not
  exist: `main.scrollTop` persists into the next view.
- `2026-08-19-mobile-viewport-and-breakpoints` (bugfix, draft) - waiting on
  `2026-08-19-content-area-spacing-and-scroll-contract`. Both edit `AppShell.vue` and
  `AgentDetailView.vue`. **Read its §7 item 1 before starting**: the `100vh` line this dossier
  targets is relocated by `shared-overlay-and-shell-defects`, so the element carrying the
  viewport height depends on what has already landed.

## In progress

- `2026-08-19-chatroom-scroll-and-composer` (bugfix) - `depends_on: []`. Started 2026-08-21 from
  base `b4b25d1`. Ten findings in the chatroom feed surface; F-29's second arm was cut at
  approval (FU-6). Six of its eighteen ACs are browser-only by construction - jsdom performs no
  layout, so the unit tier proves the arithmetic, the counting rule, the throttle and every
  wiring decision, and none of the visual outcomes.
- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.
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
