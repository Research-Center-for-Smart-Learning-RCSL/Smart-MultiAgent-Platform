---
type: audit
status: draft
created: 2026-07-03
---

# Observer Agents — Mechanism + UI Audit

Area: observer-agent feature end to end — backend (`contexts/conversation` observation
service/repo/API, `contexts/agents` turn engine observer branch, `contexts/orchestration`
wake-up path) and frontend (`slices/conversation` observer panel, release dialog,
composables, settings). Intent sources: SRS §28 (`R28.01`–`R28.14`),
`docs/observer-agents/00-overview.md`, `A-backend.md`, `B-frontend.md`.

Method: five parallel read-only investigation lenses (release lifecycle, trigger/turn
path, AuthZ/isolation, frontend data flow, UI components), followed by an independent
verification pass that re-read every cited path and attempted to refute each candidate.
Known open follow-ups from `docs/tasks/2026-07-03-conversation-bugfixes/spec.md`
(FU-3, FU-4, FU-5) were excluded, not re-reported.

## F-1: Private release pushes observation content into Redis before the DB commit

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/conversation/application/observation_service.py:192-202`
  (pending_notify.push inside `release()`), `backend/app/api/v1/observations.py:131-145`
  (commit only after `release()` returns; comment at :142 claims "Durable-commit before
  any dispatch" but the agents-target push has already happened),
  contrast `observations.py:170-212` (`_dispatch_release` correctly defers all
  room-target side effects to post-commit best-effort).
- **Failure scenario**: creator releases observation O to agents `[A]`; `release()` runs
  the CAS and RPUSHes the full content into A's `pending_notify` queue; the subsequent
  `db.commit()` fails (timeout/serialization/connection drop) → CAS and audit roll back,
  but A already holds the content. DB says unreleased; creator retries; CAS wins again;
  A receives the note a second time. Variant: pushing to `[A, B, C]` where C's push
  raises fails the whole release (409-free 500) after A and B already got the note —
  the agents path also has the opposite durability contract from the room path (Redis
  hiccup fails the release instead of being best-effort).
- **Blast radius**: single-shot delivery guarantee (R28.08) and the A.9 ordering
  discipline; phantom/double delivery of creator-authorized content to agents.
- **Intent source**: [R28.07][R28.08]; `A-backend.md` §A.9.
- **Fix direction**: move the push loop into `_dispatch_release` (post-commit,
  best-effort per target), using `result.target_agent_ids` which is already returned.

## F-2: Presence-gated wake-up leaks observer identity to non-creator owners and silently drops the observation

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `backend/contexts/orchestration/application/wakeup_service.py:96-108`
  (role-blind `every_n_messages` branch; empty room + `allow_self_open=false` →
  `_notify_wakeup_gated` + skip), `:112-166` (`_notify_wakeup_gated` sends a bell
  naming the agent — `f"{agent.name} reached its message trigger..."`, metadata
  carries `agent_id` — to every project OWNER),
  `backend/contexts/conversation/application/triggers.py:36-40` (`list_bound_agent_ids`
  has no role filter, so observer bindings flow into this path),
  `backend/contexts/orchestration/domain/models.py` (`allow_self_open` defaults False —
  this is the default config, not an edge case).
- **Failure scenario**: room R has creator Alice (plain member) and observer "Watcher"
  with default wake-up config; everyone closes the tab; messages accumulate (e.g. a
  normal agent's autonomous rounds) until Watcher's every_n hits while presence is
  empty. Every project owner (non-creators per R28.02 when a creator exists) receives
  "Watcher reached its message trigger but did not open the room..." with Watcher's
  agent_id — disclosing which agent observes R. Alice gets nothing: the observation
  is suppressed even though observer output never goes to the room, so the "don't
  reply into an empty room" rationale does not apply to observers.
- **Blast radius**: observer anonymity (the core stealth property when
  `disclose_observers=false`) and observer coverage of unattended rooms — the exact
  "what happened while I was away" use case.
- **Intent source**: [R28.09][R28.10]; `A-backend.md` §A.5 ("observers wake like any
  bound agent") never reconciled with the presence gate.
- **Fix direction**: make the wake path role-aware — observer bindings bypass the
  presence gate (their output is out-of-band) and must never trigger
  `_notify_wakeup_gated`.

## F-3: Frontend `isCreator` is broader than the backend push recipient — admin/moderator viewers get a dead "live" panel

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/composables/useObservations.ts:62-70`
  (isCreator true for `is_admin`, and for project owners in NULL-creator rooms) vs
  `backend/contexts/conversation/application/observation_service.py:97-105`
  (`recipient_user_id` returns `created_by_user_id` only; None for legacy rooms —
  "events are not fanned out to every moderator in v1"),
  `backend/contexts/agents/application/runtime/turn_engine.py` `_emit_observation_event`
  (same single recipient); panel gating `frontend/src/slices/conversation/views/ChatroomView.vue:424-426`.
- **Failure scenario**: an admin opens the Observer tab of a room whose creator is
  Alice — `observation.*` events go to Alice's user channel only. The admin's panel
  loads once over REST and then never updates: no analyzing status, no auto-refresh on
  new observations, no unread badge, release chips stale. Same for a project owner on
  a legacy NULL-creator room (the documented moderator-fallback creator), where the
  backend deliberately emits to nobody. No polling or refresh affordance compensates.
- **Blast radius**: every admin/moderator-fallback viewer of the observer panel;
  silently stale data presented as live.
- **Intent source**: [R28.02][R28.13]; `B-frontend.md` §B.2 (composable designed
  around live updates). The backend behavior is documented v1 scope — the defect is
  the frontend presenting a live surface it structurally cannot back; minimum fix is
  a refresh affordance or polling fallback when `me.id !== created_by_user_id`.

## F-4: Release dialog pending state is clobbered — no spinner, double-submit possible

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/components/ObservationReleaseDialog.vue:189-210`
  (`submit()` emits then resets `submitting=false` in `finally`; Vue emits are
  synchronous), `frontend/src/slices/conversation/views/ChatroomView.vue:453-473`
  (parent sets `setSubmitting(true)` then suspends at `await observations.release` —
  control returns to the child's `finally`, which overwrites it to false for the whole
  in-flight request).
- **Failure scenario**: creator clicks Release; the confirm button never shows a
  pending state and stays enabled; a second click passes the `submitting` guard and
  fires a second POST; the loser gets 409 → the user sees an "already released" info
  toast for an action they performed once. Server CAS protects correctness; the UX
  contract is broken.
- **Blast radius**: every release interaction; spurious duplicate requests and
  confusing toasts under double-click.
- **Intent source**: `B-frontend.md` §B.4.3 ("pending state on the button"); [R28.08].

## F-5: Unread badge stops counting on drawer layouts — `panelOpen` tracks the tab, not visibility

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/views/ChatroomView.vue:437`
  (`watch(railTab, ...)` is the only `setPanelOpen` driver), drawer visibility is the
  separate `peopleDrawerOpen` flag (`ChatroomView.vue:183-220`);
  `useObservations.ts:114-117, 147`.
- **Failure scenario**: on mobile, creator opens the drawer, selects the Observer tab,
  closes the drawer. `railTab` stays `'observer'` so `panelOpen` stays true while the
  panel is invisible; `observation.created` increments nothing (`if (!panelOpen)`),
  so the arrival badge — the only signal while not looking — never lights up.
- **Blast radius**: mobile/tablet creators.
- **Intent source**: `B-frontend.md` §B.3/§B.8.

## F-6: Observer failure kind is captured but never rendered — roster shows only "error"

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/composables/useObservations.ts:79-85`
  (`errorReason` plumbed into `ObserverEntry`), `:149-154` (`observation.failed` kind
  stored); `frontend/src/slices/conversation/components/ObserverPanel.vue:8-16`
  (template renders only the literal status, tooltip hardcoded to the generic error
  string; `errorReason` unused).
- **Failure scenario**: observer turn fails with `rate_limited` /
  `provider_exhausted:*` / `key_group_scope`; the creator sees the word "error" and
  cannot tell transient from hard failure, nor a benign skip from a real one.
- **Blast radius**: creator diagnosability of observer failures.
- **Intent source**: `B-frontend.md` §B.3 ("kinds mirror `agent.finished`").

## F-7: Deleting an observation from a full page kills "Load earlier" while older pages exist

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `useObservations.ts:99-100` (`getNextPageParam` returns a cursor only
  when `lastPage.length === PAGE_SIZE`), `:200-209` (`remove()` filters the cached
  page below PAGE_SIZE).
- **Failure scenario**: newest loaded page holds exactly 50 rows (`hasNextPage=true`);
  creator deletes one of them; TanStack recomputes `getNextPageParam` on the 49-row
  page → undefined → "Load earlier" vanishes although the server has older rows.
  Recovers only on the next full refetch.
- **Blast radius**: pagination correctness after delete.
- **Intent source**: `B-frontend.md` §B.2/§B.3.

## F-8: 422 release errors are not mapped to inline field errors

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/views/ChatroomView.vue:461-469`
  (only 409 special-cased; everything else → generic `releaseFailed`).
- **Failure scenario**: server rejects a release with an RFC 7807 422 (oversized
  override, agent no longer eligible); creator sees "Failed to release the
  observation." with no field-level cause.
- **Blast radius**: release error UX.
- **Intent source**: `B-frontend.md` §B.4.3 ("on 422 map the problem detail to inline
  field errors").

## F-9: Observer-tab unread badge lacks the planned aria wiring

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: `frontend/src/slices/conversation/views/ChatroomView.vue:428-436`
  (badge passed as bare number), `frontend/src/shared/ui/STabs.vue:105-108` (rendered
  with no aria-label/aria-live); `conversation.observers.badgeAria` absent from both
  locale files and unreferenced in `src/`.
- **Failure scenario**: screen-reader users hear a naked number on the tab and get no
  announcement when new analyses arrive.
- **Blast radius**: a11y for creator users.
- **Intent source**: `B-frontend.md` §B.7/§B.8 (badgeAria key + `aria-live="polite"`).

## Plausible findings and design tensions (not fully traced defects)

- **P-1** (plausible, design tension): observer turns bump autostop and reset only on
  user messages, so an observer stops observing a long agent-only exchange after
  `autostop_rounds` turns until a human posts — sanctioned by `A-backend.md` §A.6.5
  but at odds with §28's monitoring purpose.
  `backend/app/workers/tasks/orchestration.py:104,154-155`.
- **P-2** (plausible): benign skips (`no_input`, `empty_reply`) and hard failures share
  the single `observation.failed` event/kind, contra A.6.3's error-vs-reason contract;
  pinned by tests, so deliberate — but it makes F-6's UX problem unsolvable without a
  backend change. `turn_engine.py:1003-1006,1055-1058` vs `:836-856,1149-1151`.
- **P-3** (plausible): a cancelled observer turn (`asyncio.CancelledError`, e.g. worker
  redeploy) after `observation.started` emits no `observation.failed`; with no
  client-side watchdog (unlike the room path's `thinkingTimer`,
  `useChatroomSocket.ts:129-146`) the roster shows "analyzing" until the next event.
  `turn_engine.py:1137` (`except Exception`).
- **P-4** (plausible, spec gap): `DELETE /chatrooms/{id}/agents/{agent_id}` is
  moderator-gated with no creator check, so a non-creator owner can silently unbind a
  creator's observer — asymmetric with bind/role-change which are creator-gated.
  `backend/app/api/v1/chatrooms.py:447-468` vs `:398-402,427-428`.
- **P-5** (plausible, spec letter vs implementation): a creator demoted below project
  owner loses `RESOURCE_CREATE_EDIT` and 403s before the per-field creator gate, so
  they can no longer toggle `disclose_observers` despite R28.09's "only the creator
  can change it". `chatrooms.py:285-295`.
- **P-6** (plausible, authz staleness): `is_room_creator` never rechecks current
  project membership, so a creator removed from the project retains observation
  read/release/delete and role management. Matches R28.02's letter.
  `backend/contexts/conversation/application/access.py:139-155`.
- **P-7** (plausible, spec ambiguity): guests with `allow_guest_links` read receive
  `observers_present`/`disclose_observers`/`created_by_user_id` in the room DTO;
  R28.02 says guests are always denied observer surfaces, R28.09 frames the indicator
  as transparency. Needs a spec ruling. `chatrooms.py:115-130,263-272`.
- **P-8** (minor polish): release failure is double-surfaced (inline SAlert + toast
  from the same `setError`). `ObservationReleaseDialog.vue:218-221`.
- **P-9** (minor): `content_override` accepts whitespace-only strings
  (`Field(min_length=1)`) which become the released message body verbatim.
  `observations.py:52`, `observation_service.py:138`.

## Follow-ups routed out of scope

- **FU-1**: `B-frontend.md` §B.9 mandates `useObservations.test.ts`; the file does not
  exist — the composable holding the live-update/pagination/isCreator logic (where
  F-3/F-5/F-7 live) has no direct unit coverage.
- **FU-2** (pre-existing, not observer-specific): `ChatroomAgentRepository.list`
  (`chatroom_repo.py:240-255`) has no ORDER BY under offset/limit pagination.
- **FU-3** (fragile coupling, not a defect): the frontend applies `ev.target` from the
  `observation.released` payload directly (`useObservations.ts:155-158`); R28.13's
  "ids-only" wording and this payload disagree — trimming the payload later would
  silently blank release chips.

## Coverage

Covered: observation creation/release/delete lifecycle and CAS, pending_notify path and
TTL semantics, trigger gating (every_n, silence, mention exclusion, autostop, release
wake), turn-engine observer branch (zero-room-emit, own-memory window, error paths,
role re-resolution), creator/AuthZ resolution and all observer endpoints, disclosure
computation, audit content exclusion, export/search/compaction leak surfaces, frontend
composables/store/panel/dialog/settings/i18n (both locales)/DOMPurify pipeline.

Not covered: integration/wiring behavior under real Postgres/Redis (analysis was
static; no live reproduction), Vault/key-scope interactions of observer turns beyond
the error-path reading, load/perf characteristics, and the notification context
internals beyond `_notify_wakeup_gated`.

Verification: every F-n above survived an independent refutation pass with the failure
scenario fully traced; candidates that could not be traced were demoted to P-n. The
leak-proof suite (`test_observer_agents.py`) pins the core isolation guarantees —
observation content never reaches room surfaces; no cross-room or cross-tenant read
path was found.
