---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R28.02, R28.03, R28.05, R28.06, R28.07, R28.14]
depends_on: []
---

# Observations are stranded when the last observer binding is removed

`depends_on: []` is justified: the fix is confined to the observer surface in
`frontend/src/slices/conversation/` and touches no file, query key, socket handler or
backend route claimed by another dossier in the 2026-07-22 hand-off map
(`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:668-683`). In particular it
does not depend on F-1 (socket lifecycle): the defect and its fix are both independent of
whether the socket reconnects, because the observer tab's visibility is derived from a REST
query and a bindings query, not from a WS frame. Nothing here needs the reconnect-reconciliation
group to land first, and nothing here changes what that group reconciles.

## 1. Summary

When the room creator removes the last observer binding — by unbinding the agent, or by flipping
its role from `observer` to `normal` — the Observer tab disappears from the chatroom rail
entirely. It does not render empty: it is removed from the tab list. Every observation that
observer already wrote stays live in `agent_observations`, unreleased and undeleted, with no UI
affordance anywhere in the application to read it, release it into the room (R28.06), release it
privately to an agent (R28.07), or soft-delete it (R28.14). The backend read path is unchanged
and still serves those rows to the same creator; only the client's route to them is gone. The
creator's own analyses become unreachable through the product, recoverable only by rebinding some
agent as an observer to make the tab reappear.

## 2. Observed vs Expected

- **Observed** — the Observer tab is gated on the binding roster and nothing else.
  `frontend/src/slices/conversation/views/ChatroomView.vue:441-443` computes
  `showObserverTab = observations.isCreator.value && observations.observerAgents.value.length > 0`,
  and `:452-467` includes the `observer` entry in `railTabs` only when that computed is true.
  `observerAgents` is derived purely from bindings —
  `frontend/src/slices/conversation/composables/useObservations.ts:75-90` filters
  `opts.boundAgents` on `role === 'observer'` — and never consults the observations themselves.
  `ObserverPanel` is mounted in exactly two places, both inside `<template #tab-observer>` of the
  conditional `STabs` (`ChatroomView.vue:151-163` desktop rail, `:208-220` mobile/tablet drawer);
  no route, admin view or settings page renders it, and no other component calls
  `listObservations`. Meanwhile the rows are untouched: `ChatroomService.remove_agent`
  (`backend/contexts/conversation/application/chatroom_service.py:368-400`) calls only
  `self._agents.remove` plus an audit emit, and `set_agent_role` (`:318-366`) only CASes the role
  column and audits — neither touches `agent_observations`.
- **Expected** — the creator retains read, release and soft-delete access to observations that
  already exist, regardless of the current binding roster. [R28.03] makes the observation
  "readable only by the room creator per R28.02 resolution" — a rule about *who*, with no clause
  about bindings. [R28.06] and [R28.07] grant the creator release of *an observation*, not of an
  observation belonging to a currently-bound observer. [R28.14] grants soft-delete, which is the
  only sanctioned way an observation leaves the creator's view. The backend implements exactly
  this: `_require_creator` (`backend/app/api/v1/observations.py:106-114`) resolves room access and
  the creator gate and never consults bindings, and `ObservationRepository.list`
  (`backend/contexts/conversation/infrastructure/repositories/observation_repo.py:91-137`) filters
  on `chatroom_id` and `deleted_at` only. The client is the sole layer that adds a binding
  precondition the requirements do not contain.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should removing the last observer binding cascade a soft-delete over that room's observations, instead of restoring the read path? | No. Preserve the rows; fix the read path. | [R28.14] makes deletion an explicit creator act, so an implicit one contradicts it. A role flip is reversible, and [R28.05]'s memory window deliberately restores an agent's own prior observations on rebinding (`observation_repo.py:139-165` consumed at `backend/contexts/agents/application/runtime/turn_engine.py:2257-2259`), so a cascade would silently destroy that. Cascading would also destroy content the creator may still legitimately release under [R28.06]/[R28.07], converting a display defect into permanent data loss. |
| Q-2 | Should already-released observations count toward making the tab visible, or only unreleased ones? | All non-deleted rows count. | `ObservationRepository.list` filters only `deleted_at` (`observation_repo.py:98-101`) and the panel renders release chips from `release_target`; release history is part of what [R28.06]/[R28.07] leave the creator to read. Adding an unreleased-only filter would invent a second, narrower notion of "has observations" that exists nowhere in the backend. |
| Q-3 | Do observations already stranded in production need a data-repair migration? | No, and none is permitted. See §7. | The stranded rows are byte-identical to correct rows; no column holds a wrong value and no invariant is violated. "Stranded" is a property of the client read path, not of the data. Deploying the fix makes every existing stranded observation reachable again with zero writes. |
| Q-4 | Does this warrant a `check-security` referral, as F-12 of the source audit did? | No. See the access-control analysis in §6. | Nothing new is disclosed to anyone. The set of principals who can read these rows is unchanged before and after the unbind, and the fix widens it no further — it restores a client path to data the server already serves to that same principal. |
| Q-5 | Should the corrected gate live in `ChatroomView.vue` or in `useObservations.ts`? | In the composable, exported as `hasObserverSurface`. | The composable already owns every other piece of observer state — `isCreator` (`useObservations.ts:63-71`), the roster (`:75-90`), the observations cache (`:94-116`). The view re-deriving a fourth piece from two of the composable's own outputs is the SoC inversion that produced the defect. |

## 4. Reproduction

Preconditions: a project with at least one agent `W`; a chatroom `R` created by user `C`
(`chatrooms.created_by_user_id = C`, so `isCreator` resolves through the direct-match branch at
`useObservations.ts:68` and not the moderator fallback); `C` signed in as a normal member, not an
admin.

1. As `C`, open `R`'s settings and bind `W` with role `observer`
   (`useChatroomBindings.onAddAgent`, `frontend/src/slices/conversation/composables/useChatroomBindings.ts:92-110`).
2. Post messages in `R` until `W`'s `every_n_messages` trigger fires at least once, so at least
   one row lands in `agent_observations`. Do not release it and do not delete it.
3. Open `R`. Confirm the Observer tab is present in the right rail and the observation renders in
   `ObserverPanel`.
4. Return to settings and either flip `W` to `normal` (`useChatroomBindings.onSetRole`, `:112-124`)
   or unbind it (`onRemoveAgent`, `:126-138`). Both paths call `loadBindings` on success, so
   `boundAgents` loses the observer entry.
5. Reopen `R`.

Observed at step 5: the rail shows the plain presence panel or the People/Activity tabs only; the
Observer tab is absent from `railTabs` (`ChatroomView.vue:452-467`). No empty state, no
explanation, no route to the observation.

Deterministic; no timing, ordering or concurrency component. Confirming the row survives:
`GET /api/chatrooms/{R}/observations` as `C` still returns it — `_require_creator`
(`backend/app/api/v1/observations.py:106-114`) passes and the repository query
(`observation_repo.py:91-137`) has no binding predicate.

## 5. Root Cause Analysis

1. **Trigger.** `C` removes the last `observer`-role binding. `ChatroomService.remove_agent`
   (`chatroom_service.py:368-400`) or `set_agent_role` (`:318-366`) mutates `chatroom_agents` and
   audits; neither reads or writes `agent_observations`. This is correct behavior per Q-1 and is
   **not** the root cause.
2. `loadBindings` (`useChatroomBindings.ts:74-90`) refetches, and the room view's
   `boundAgentsQuery` supplies `useObservations` with a roster no longer containing `W`
   (`ChatroomView.vue:430-435`).
3. `observerAgents` (`useObservations.ts:75-90`) filters that roster on `role === 'observer'` and
   yields an empty array. This computed is correctly named and correctly implemented — it is a
   *roster*, and the roster is genuinely empty.
4. **Root cause.** `ChatroomView.vue:441-443` uses that roster as the sole predicate for whether
   the observation *surface* exists: `showObserverTab = isCreator && observerAgents.length > 0`.
   The visibility of a data surface is derived from a liveness signal about producers rather than
   from the presence of the data. This is the earliest link whose correction prevents the symptom:
   fix it and steps 1-3 remain exactly as they are, while the tab persists. Every link downstream
   (`railTabs` at `:452-467`, the two `ObserverPanel` mounts at `:151-163` and `:208-220`) merely
   propagates this one decision.
5. **Symptom.** The `observer` entry is omitted from `railTabs`, so both `ObserverPanel` mounts
   are unreachable, and with them the release dialog (`openRelease`, wired at `:159`) and the
   delete action (`onObservationDelete`, `:160`).

**Aggravating factor, not cause.** The observation query is still running and still holding the
data. `observationsQuery` is gated on `isCreator` alone (`useObservations.ts:104`) — never on
bindings — so on every room open, every creator already fetches the observations that the tab
then refuses to expose. The information needed to make the correct decision is in the composable's
own cache at the moment the wrong decision is made. That is what makes this a wiring defect rather
than a missing capability.

**Provenance — new, not a regression, not a noted gap.** The gate is original design, not drift:
`docs/observer-agents/B-frontend.md:140-141` prescribes it verbatim — "the Observer tab renders
only when `isCreator && observerAgents.length > 0`" — and `ChatroomView.vue:441-443` implements
that faithfully. So the implementation is correct against its design note, and the design note is
wrong against [R28.03]/[R28.06]/[R28.07]/[R28.14]. The closed audit
`docs/audits/2026-07-03-observer-agents-audit/findings.md` does not cover this: its F-1..F-10 and
P-1..P-9 concern release durability, wake-up gating, panel liveness for non-recipient viewers,
dialog pending state, badge visibility, pagination after delete, error mapping and unbind
authority — F-7 (`:149-160`) is pagination collapse after an observation delete, and P-4
(`:220-224`) is *who may* unbind an observer, neither of which is this. Its FU-1 (`:244-246`)
noted `useObservations.test.ts` did not exist; that file exists today
(`frontend/src/slices/conversation/__tests__/useObservations.test.ts`), which is why §8 can extend
it rather than create it. `docs/audits/2026-07-22-agent-config-runtime/findings.md:62-63`
re-verified that audit's leak class as still holding, and this defect is not in that class — it is
the opposite failure mode, an under-exposure rather than a leak. The closest prior art is the
design risk register, `docs/observer-agents/00-overview.md:237`: "Observer analyses go
stale/unbounded — Soft-delete endpoint; own-memory window bounded; retention hook listed as v2."
The design named the soft-delete endpoint as the mitigation for accumulating analyses and never
checked that the endpoint stays reachable. This defect is precisely the case where it does not.
**Verdict: genuinely new, and it invalidates a mitigation the design register recorded as closed.**

## 6. Blast Radius and Sibling Suspects

### Blast radius

Every creator of every room where the last observer binding was removed or demoted while
non-deleted observations existed, in every tenant, retroactively — the condition is permanent
until an observer is rebound. There is no time bound and no self-healing path.

### What "stranded" actually costs

The severity turns on whether these are inert orphans or something still live, so each arm was
checked separately.

- **Orphaned rows in the FK sense: no.** `agent_observations` carries no foreign key to
  `chatroom_agents`. Its only FKs are `chatroom_id` and `agent_id`
  (`backend/contexts/conversation/infrastructure/tables.py:88-95`, matching the authoritative DDL
  at `docs/observer-agents/00-overview.md:122-139`), and both parents still exist after an unbind.
  Every row remains fully referentially valid. There is no dangling state to clean.
- **Still readable, by whom: by exactly the same principal, and nobody else.**
  `GET /chatrooms/{id}/observations` keeps serving these rows —
  `_require_creator` (`backend/app/api/v1/observations.py:106-114`) resolves room access, applies
  the read gate, then the creator gate, and consults no binding; the repository query
  (`observation_repo.py:91-137`) filters `chatroom_id` and `deleted_at` only. Crucially, the set of
  principals able to read the rows is **identical before and after** the unbind: creator per
  [R28.02] resolution, plus admin bypass. Removing a binding neither widens nor narrows it. **This
  is therefore a reachability defect, not an access-control defect, and no `check-security`
  referral is warranted** (Q-4). Contrast F-12 of the source audit
  (`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:361-382`), which was routed to
  `check-security` precisely because a principal gained a capability over another subject's data.
  Nothing analogous happens here.
- **Still counted somewhere: yes, in exactly one place, and it is by design.**
  `ObservationRepository.list_recent_for_agent` (`observation_repo.py:139-165`) filters on
  `chatroom_id`, `agent_id` and `deleted_at` — no binding predicate — and feeds the observer
  self-memory block ([R28.05]) via `turn_engine.py:2257-2259`, reached only when `is_observer` is
  true (`turn_engine.py:1793`). Consequence: if the same agent is later rebound as an observer in
  the same room, its stranded observations silently re-enter its context window. That is the
  documented cumulative-memory semantic, not a defect — but it is a second, independent reason not
  to cascade-delete on unbind (Q-1), and it means the rows are not inert.
- **Still driving a UI affordance elsewhere: no, and the one adjacent indicator is correct.**
  `observers_present` in the chatroom DTO is computed from live bindings
  (`backend/app/api/v1/chatrooms.py:133`, `bool(not viewer_is_pure_guest and r.disclose_observers
  and has_observers)`), so the neutral [R28.09] disclosure chip correctly goes false when the last
  observer is unbound. No stale "observers enabled" indicator is left behind, and no non-creator
  surface changes at all.

Net: minor, matching the source audit's verdict at
`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:318`. The cost is the creator losing
product access to their own private analyses, plus the [R28.14] deletion right becoming
unexercisable — which is the same right the design register leaned on as its mitigation for
unbounded observation growth.

### Sibling suspects

Swept for the shape "removing a binding leaves derived rows behind with no cleanup and no
surviving read path".

| Site | Verdict | Evidence |
|---|---|---|
| Observer binding removed → `agent_observations` | **Confirmed** — this defect | §5 |
| Activity tab in the same computed | **Cleared** | `ChatroomView.vue:444-446` computes `showActivityTab = isCreator \|\| !!activation`. The disjunction means an ended activation never hides the creator's access to the activity surface. The correct shape already exists three lines below the defective one in the same file. |
| Agent hard-deleted → its bindings and its observations | **Cleared** | Both cascade together: `chatroom_agents.agent_id` is `ON DELETE CASCADE` (`tables.py:69`) and `agent_observations.agent_id` is `ON DELETE CASCADE` (`tables.py:92`). Binding and derived rows die in the same statement; no window exists in which one outlives the other. |
| Chatroom deleted → observations | **Cleared** | `agent_observations.chatroom_id` is `ON DELETE CASCADE` (`docs/observer-agents/00-overview.md:124`). The parent's disappearance takes the derived rows with it. |
| Observer unbound → `released_observation` notes already queued to a normal agent | **Confirmed same shape, self-clearing; carried as FU-1, not fixed here** | `pending_notify` is keyed by agent id alone (`backend/contexts/orchestration/infrastructure/pending_notify.py:28-29`), so a private release queued to agent `X` for room `R` survives `X`'s unbind from `R`. It cannot leak: the turn engine requeues any note whose room does not match the turn's room, and does so explicitly to prevent cross-room disclosure (`turn_engine.py:1571-1577`). And it cannot persist: the key carries a 24h TTL (`pending_notify.py:25`). Bounded and non-disclosing, so not a defect of this class — but it is the same "the binding went away and the derived artifact did not" family, and it is the tail of F-21 of the source audit (`findings.md:552-567`), which the presence-transition dossier owns. |
| RAG / Knowledge-Map config soft-deleted → agents left pointing at it | **Cleared, already fixed** | FK `ON DELETE SET NULL` (migrations 0012 / 0048, per `backend/tests/unit/test_config_delete_agent_unbind.py:1-3`) plus a one-time repair migration for rows written before the fix (`backend/alembic/versions/0054_config_delete_agent_unbind.py:12`). Cited again in §7 as the repair precedent that deliberately does **not** apply here. |
| Skill unbound from an agent → per-binding derived rows | **Cleared** | `BindingService.unbind` (`backend/contexts/skills/application/binding_service.py:431-439`) clears the binding row and returns the `Skill` for auditing; nothing else is keyed on the (agent, skill) pair. Skill bytes belong to the skill, not to the binding, so an unbind produces no derived remainder. |
| Activation window ended → open `activity_sessions` | **Cleared as by-design** | Force-closing participant sessions is an explicit non-goal recorded at `docs/tasks/2026-07-13-activities-activation-ux/spec.md:49-51`, and the consequence is already carried as FU-2 of the source audit (`findings.md:711-713`). Not a defect to re-file. |

### The retention emit-nothing parallel

`docs/audits/2026-07-22-conversation-verification-gap/findings.md:491-495` (FU-2) records that the
retention purge hard-deletes messages and publishes nothing:
`backend/contexts/conversation/application/retention_service.py:91-93` executes
`t.messages.delete()` and the surrounding code emits only `audit.emit` (`:95-107`) — the module
imports no `Publisher` — while `frontend/src/slices/conversation/utils/mergeMessages.ts:10-11`
documents that out-of-window deletions "arrive via the `message.deleted` WS event".

**The parallel is real as a family but does not extend to the fix.** Both are state changes whose
read side is never told. The difference is decisive: the retention purge changes state in a
background worker, for viewers who are not present and cannot be asked to refetch, so its read
side genuinely requires an emit (or a corrected comment). The observer unbind is performed by the
creator, in their own session, through the settings view, and `loadBindings`
(`useChatroomBindings.ts:74-90`) refetches immediately on success while the room view refetches
bindings on mount. No one is deprived of a notification. **Therefore the correct fix here is
explicitly not to add an event** — it is to stop needing one, by deriving the surface's visibility
from data the client already holds. Adding a `binding.removed` frame would be the masking fix: it
would repair the same-session case that already works and leave a creator who returns to the room
tomorrow with the tab still missing. Noted here so the parallel is not mistaken for a shared
remedy.

## 7. Fix Design

Frontend only. No migration, no API change, no backend change.

**1. Derive the surface from the data, in the composable that owns the data.**
Add to `frontend/src/slices/conversation/composables/useObservations.ts` a computed
`hasObserverSurface`, true when `isCreator` and either the roster is non-empty or at least one
non-deleted observation is cached, and export it from the return block (`:232-245`) alongside the
existing `isCreator` / `observerAgents` / `observations`.

**2. Consume it in the view.** `ChatroomView.vue:441-443` becomes a read of
`observations.hasObserverSurface.value`; `railTabs` (`:452-467`) and both `ObserverPanel` mounts
(`:151-163`, `:208-220`) are unchanged and inherit the correction. `showActivityTab` (`:444-446`)
and `showRailTabs` (`:447`) are untouched.

**3. Give the panel a roster-empty affordance.** With the tab restored, `ObserverPanel.vue:3-16`
would render an empty roster `<ul>` above the divider with no explanation. Add an inline note —
`SAlert`, consistent with the panel's existing inline-not-toast discipline
(`docs/observer-agents/B-frontend.md:182-187`) — rendered when the roster is empty and
observations are present, stating that no observer is currently bound and these are past
analyses, which remain releasable and deletable. New keys under `conversation.observers` in
**both** `frontend/src/slices/conversation/locales/en.json` and `.../zh-TW.json` (the existing
`observers` block sits at `zh-TW.json:167-181`; `emptyTitle`/`emptyText` are at `en.json:186-187`).
No hardcoded strings.

### Why this corrects rather than masks

The gate and the correction sit at the same link in the causal chain. §5 names
`ChatroomView.vue:441-443` as the earliest correctable link: with the fix, the trigger, the
binding refetch and the empty roster all still happen exactly as before, and the symptom does not.
Nothing downstream is compensating for anything upstream.

The fix also invents no new source of truth. `observationsQuery` is already gated on `isCreator`
alone (`useObservations.ts:104`), so the creator already fetches these rows on every room open —
today's code holds the answer in cache and consults the wrong value. The change adds zero HTTP
requests, zero query keys, zero events, and zero server state. A masking fix would look like
forcing the tab always-on for creators (hides the defect behind an unconditional affordance and
shows an empty panel in the common case), or emitting a WS frame on unbind (see the retention
parallel in §6 — repairs only the same-session case).

### Rejected alternative: cascade cleanup on unbind

Soft-deleting the room's observations inside `remove_agent` / `set_agent_role`
(`chatroom_service.py:318-400`) was rejected on three independent grounds, any one sufficient:
[R28.14] makes deletion an explicit creator act; [R28.05]'s memory window
(`observation_repo.py:139-165` → `turn_engine.py:2257-2259`) deliberately restores an agent's own
history when it is rebound, which a cascade would silently destroy; and it would destroy content
the creator may still release under [R28.06]/[R28.07]. It converts a display defect into
irreversible data loss, which is the wrong direction for a minor-severity finding.

### Data repair for observations already stranded

**None, and none is permitted.** This is the operative decision of the dossier, so it is stated
precisely:

- The stranded rows are byte-identical to correct rows. No column holds a wrong value; no
  invariant is violated; no referential integrity is broken (§6). "Stranded" describes the
  client's read path, not the data.
- There is no state to repair *to*. A repair would have to either soft-delete the rows — which is
  Q-1's rejected cascade, executed retroactively and at greater scale — or re-create a binding
  the creator deliberately removed. Both destroy or fabricate user intent.
- Deploying the frontend change makes every already-stranded observation reachable again, in
  every affected room, in every tenant, with zero writes. The defect is fully retroactively
  self-healing on deploy.
- The contrast with the accepted precedent is exact: `0054_config_delete_agent_unbind.py:12` shipped
  a one-time repair "to null the bindings that were already" left pointing at deleted configs —
  correct there because rows carried *wrong values* that no code change could reinterpret. Here no
  row carries a wrong value.

**No Alembic revision is part of this dossier.** If /build finds itself writing one, the fix has
drifted into the rejected cascade and must stop.

## 8. Regression Test Plan

Failing tests first. AC-1 applies to T-1, T-3 and T-4, each of which fails against current code;
T-2 and T-5 are guards that pass today and must keep passing.

**T-1 (fails today) — `frontend/src/slices/conversation/__tests__/useObservations.test.ts`**, new
case `"exposes the observer surface when observations exist but no observer is bound"`. Mount the
composable through the file's existing harness (`:86-122`), overriding `boundAgents` to
`[{ agent_id: NORMAL_AGENT, role: 'normal' }]` — the default is
`[{ agent_id: OBS_AGENT, role: 'observer' }]` at `:101-102` — while the `listObservations` mock
returns one row. Assert `hasObserverSurface.value === true` and, to pin that the roster itself is
correctly empty, `observerAgents.value.length === 0`. **Fails today** because
`useObservations.ts:232-245` exports no such value: the property is `undefined` and the assertion
fails on the first expectation. The composable has no concept of the surface at all today; that is
the gap.

**T-2 (passes today, guard against over-correction) — same file**, new case `"keeps the observer
surface hidden for a creator with neither bindings nor observations"`. Same harness with
`boundAgents: []` and `listObservations` resolving `[]`; assert `hasObserverSurface.value === false`.
This pins that the fix does not degrade into "always visible for creators", which would be the
masking variant rejected in §7. Assert with `toBe(false)` rather than `toBeFalsy()` so the
pre-fix state (`undefined`) does not accidentally satisfy it — which makes this a second failing
test before the fix.

**T-3 (fails today) — `frontend/src/slices/conversation/__tests__/ChatroomView.test.ts`**, new case
`"renders the Observer tab for a creator whose observations outlived the last observer binding"`.
Render the view for a creator with `boundAgentsQuery` returning only normal-role bindings and the
observations endpoint returning one row; assert the observer tab is present in the rendered rail
and that `ObserverPanel` is mounted. **Fails today** because `ChatroomView.vue:441-443` reads only
`observerAgents.length`, so the `observer` entry is never pushed into `railTabs` (`:452-467`) and
the panel is never mounted. Note for /build: a grep for `observer` in this file currently returns
**no matches** — the tab gate has zero test coverage today, which is why the defect survived two
prior passes over this exact region (the 2026-07-03 frontend-fixes dossier's F-3 and F-5 both
edited lines within twenty of it, per the W-3 comment at `:468-472`). Adding this case closes that
hole, not just this bug.

**T-4 (fails today) — `frontend/src/slices/conversation/__tests__/ObserverPanel.test.ts`**, new
case `"explains an empty roster when past observations remain"`. Mount `ObserverPanel` with
`observerAgents: []` and one observation; assert the new note is rendered *and* that the
`ObservationCard` list still renders (`ObserverPanel.vue:41-53`), so the note supplements rather
than replaces the content. **Fails today** because no such branch exists in
`ObserverPanel.vue:1-68` — an empty roster renders an empty `<ul>` (`:3-16`) followed by the
divider, with the `SEmptyState` at `:34-39` correctly not firing since observations are present.

**T-5 (passes today, characterization) — `backend/tests/unit/test_observer_agents.py`**, new case
`"unbinding an observer leaves its observations readable"`. Assert that after
`ChatroomService.remove_agent` for an observer binding, `ObservationService.list` for that room
still returns the rows, and that the observation repository is never invoked during the unbind.
This pins §7's rejected alternative: it is the test that fails loudly if anyone later implements
the cascade. It passes against current code — `chatroom_service.py:368-400` calls only
`self._agents.remove` and `audit.emit` — and is included because the backend behavior this dossier
depends on is currently unpinned by any test.

## 9. Risks and Rollback

- **The tab reappears for rooms where it had settled into being absent.** Intended, and the
  visible consequence of the fix. A creator who removed an observer months ago and has forgotten
  the analyses will see the tab return. Mitigated by T-4's inline note, which explains why, and by
  the fact that the designed exit is now reachable: soft-deleting the observations ([R28.14],
  `observation_repo.py:219-236`) removes the last one and the tab goes away permanently and
  correctly. Before the fix that exit did not exist.
- **Tab appearance may lag mount by one round-trip.** `hasObserverSurface` turns true when
  `observationsQuery` resolves. The tab can appear a beat after the rail paints. Acceptable: it
  only ever appears, never disappears, so no affordance is yanked out from under a click. If /build
  finds the flicker objectionable, the acceptable remedy is to keep the tab mounted once true for
  the lifetime of the view — not to block the rail on the query.
- **No added network cost.** `observationsQuery` is already `enabled: isCreator`
  (`useObservations.ts:104`), so every creator already issues this request on every room open. The
  fix reads a value that is already fetched.
- **No non-creator surface changes.** `hasObserverSurface` retains the `isCreator` conjunct, so
  no non-creator gains a tab, an indicator or a request. `observers_present`
  (`backend/app/api/v1/chatrooms.py:133`) is untouched, so the [R28.09] disclosure chip behaves
  exactly as before.
- **Rollback.** Frontend-only, single revert. No migration, no schema change, no API change, no
  persisted state written by the fix, and nothing for a rollback to reconcile — reverting returns
  the product to the current (defective) behavior with no data consequence whatsoever. This is a
  direct corollary of the §7 data-repair position.

## 10. Acceptance Criteria

- [ ] AC-1: the regression tests T-1, T-2, T-3 and T-4 from §8 fail before the fix and pass after.
- [ ] AC-2: `useObservations` exports `hasObserverSurface`, true exactly when the caller is the
  creator and at least one of (observer bindings, non-deleted observations) is non-empty.
- [ ] AC-3: `ChatroomView.vue` derives the Observer tab from `hasObserverSurface`; the desktop rail
  (`:151-163`) and the mobile/tablet drawer (`:208-220`) both honor it, with no second gate
  reintroduced at either mount site.
- [ ] AC-4: with observations present and no observer bound, the panel renders the observation
  list plus an inline roster-empty note; release and soft-delete both work end to end from that
  state, exercising [R28.06]/[R28.07]/[R28.14].
- [ ] AC-5: no non-creator surface changes — a non-creator member and a guest see no observer tab,
  no note, and no observations request; `observers_present` behavior is unchanged.
- [ ] AC-6: **no Alembic revision, no backfill and no data-mutating script is added by this
  change**, and the backend diff is limited to T-5's test file.
- [ ] AC-7: T-5 passes, pinning that unbinding an observer leaves its observations intact and does
  not touch the observation repository.
- [ ] AC-8: new i18n keys exist in both `en.json` and `zh-TW.json`; no bare string literals in the
  template (gate 12).
- [ ] AC-9: `docs/observer-agents/B-frontend.md` §B.3 is corrected per §11 in the same change, so
  the design note no longer prescribes the defective gate.
- [ ] AC-10: full frontend gate green — `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build`.

## 11. SRS Delta

**SRS: none.** `REQUIREMENTS.md` §28 is correct as written and is the intent source that convicts
the code. [R28.03] (`REQUIREMENTS.md:2057`) scopes readability to the creator per [R28.02]
resolution with no binding clause; [R28.14] (`:2061`) grants soft-delete unconditionally;
[R28.06]/[R28.07] (`:2065-2067`) grant release of an observation, not of a currently-observed one.
Nothing in §28 requires amendment.

**Design notes: two corrections required**, because the defect originates in them.

1. `docs/observer-agents/B-frontend.md:140-141` currently reads that the Observer tab "renders only
   when `isCreator && observerAgents.length > 0`". Correct to: the tab renders when the viewer is
   the creator and the room has either an observer binding or at least one non-deleted observation
   — observations outlive their producer's binding by design, and the panel is the only route to
   the [R28.06]/[R28.07]/[R28.14] affordances. Add the roster-empty note to the §B.3 states list at
   `:182-187`.
2. `docs/observer-agents/00-overview.md:237` lists "Observer analyses go stale/unbounded" as
   mitigated by the soft-delete endpoint. Amend to record that the mitigation is conditional on the
   creator retaining a route to that endpoint, and that the route must not be gated on live
   bindings. This is the risk row the defect invalidated.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — Queued `released_observation` notes survive the target agent's unbind.
  `pending_notify` is keyed by agent id alone
  (`backend/contexts/orchestration/infrastructure/pending_notify.py:28-29`), so a note for room `R`
  outlives the recipient's unbind from `R`. It cannot leak — the turn engine requeues room-mismatched
  notes explicitly to prevent that (`turn_engine.py:1571-1577`) — and it expires within 24h
  (`pending_notify.py:25`). Same family as this defect, bounded rather than defective, and the
  tail of F-21 (`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:552-567`), which
  `docs/tasks/2026-07-22-presence-transition-and-release-wakeup/` owns. Worth a look there rather
  than a dossier of its own.
- **FU-2** — The observation read path has no retention hook. `docs/observer-agents/00-overview.md:237`
  defers it to v2, and a repo-wide sweep found no purge of `agent_observations`:
  `RetentionService` operates on `t.messages` only (`retention_service.py:91-93`). Observations
  therefore accumulate without bound, with soft-delete as the sole reduction mechanism — now
  reachable again, but manual and per-row. Raise as a design item when observation volume warrants it.
- **FU-3** — `ChatroomView.test.ts` has no observer coverage at all today (grep for `observer`
  returns no matches). §8's T-3 adds the one case this fix needs. The remaining observer-related
  view logic — `railTabs` badge wiring (`:452-467`), the W-3 panel-visibility watch (`:473-476`),
  `releasableAgents` (`:479-483`) — stays untested at the view level. Cleared-but-fragile; worth
  hardening in a later pass rather than expanding this dossier's scope.
- **FU-4** — The retention purge publishes nothing while the client documents a `message.deleted`
  event (`docs/audits/2026-07-22-conversation-verification-gap/findings.md:491-495`). Analyzed in
  §6 as a related family but a distinct remedy; already routed to
  `docs/tasks/2026-07-22-retention-sweep-fixes/` via V-5. Recorded here only so the parallel is
  not rediscovered and mistaken for part of this fix.
</content>
