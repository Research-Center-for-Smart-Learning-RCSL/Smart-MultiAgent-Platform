---
type: feature
status: in-progress
created: 2026-10-22
requirements: [R6.03, R13.06a, R19.04]
depends_on: []
---

# Classroom simulator: multi-participant live runs from one machine

## 1. Summary

A live classroom test of the course packs currently needs several people at several machines.
The single-student staging runs of 2026-10-20 (phase 2 dossier D-17, D-18) could not exercise
simultaneous candidates, a connected student who never speaks, two identities sharing a name, or
the group consent path, and `ac20-live-run-protocol.md` therefore requires four people and a
facilitator. This feature adds an operator-run tool, the classroom simulator, under
`frontend/sim/`. One operator on one machine points it at staging or the local compose stack. It
provisions reusable simulated student accounts, opens one isolated Playwright browser context
per participant, and drives every participant through a timed scenario. A participant is driven
by script steps, by an LLM persona that reads the room and decides when to speak, by both, or by
the operator in their own browser. Afterwards it collects the transcript, the wake-up audit rows
and the group proposals into a run directory. The first scenario it must express is the AC-20
protocol of `2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence`.

## 2. Goals and Non-goals

**Goals**

- One operator on one machine runs a live classroom of up to ten participants (registered
  students, guests and a facilitator) against staging or the local compose stack, each a
  distinct identity in the room.
- Scenarios are files. A step fires at a time offset, on a room condition (for example the next
  agent message matching a pattern), or both, and the run records planned and actual times.
- The available actions cover the classroom protocols: speak, type and pause without sending,
  stay silent, connect late, leave, open the activity tab, submit an individual activity, propose
  a group answer, approve, reject, withdraw, and for the facilitator start and end an activation.
- LLM-driven student personas (OpenAI and Anthropic) take part in discussion with their own
  cadence, within a hard per-run budget, on a key the operator supplies.
- The tool provisions and reuses its own simulated accounts, project memberships and Member
  Group, and can create the room with its agent bindings, grant and guest link.
- Every run leaves an evidence directory from which a dossier D-n entry can be written without
  running database queries on the server.
- The tool leaves the platform as it found it: a raised rate limit is always restored, including
  after a crash.

**Non-goals**

- An in-product feature. Nothing in the SMAP UI or API changes; no endpoint, migration or
  application source edit is part of this task (selectors use roles, labels and existing
  classes, Q-9).
- Load or stress testing. The design target is classroom size (up to ten participants); no
  throughput figures are claimed.
- Automatic grading of pass criteria that need judgement (whether the teacher "named both
  candidates"). The run reports mechanical facts only; an LLM judge is FU-1.
- Running live scenarios in CI. The tool's unit tests run in CI; scenarios run only on an
  operator's command.
- The production deployment. Targets are staging and local only (§8).
- Gemini or other providers for LLM personas, and LLM-driven form actions (personas speak;
  forms, votes and activations are script steps).
- Deleting simulated accounts. They are kept for reuse; removal stays an admin task.
- Mobile viewports and visual verification.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How far should the tool go? | **Confirmed 2026-10-22:** scripted timelines and LLM-driven students in one dossier. | The user wants classroom realism (free discussion the teacher agent must control) as well as reproducible protocol runs. |
| Q-2 | Which environments? | **Confirmed 2026-10-22:** staging and local compose, selected by a target configuration. | AC-20 needs staging; scenario development needs a local loop. |
| Q-3 | All participants share one client IP, and login, refresh, ws-ticket and every `/api/guest/` call share the AUTH bucket of 10 per minute per IP (`backend/app/api/middleware/rate_limit.py:61-69`, `backend/app/config/settings.py:418-428`). How is that handled? | **Confirmed 2026-10-22:** the tool raises the `auth` policy through `PATCH /api/admin/rate-limits/auth` for the run and always restores the recorded original. | Staggering alone stalls near ten participants, and the 15-minute access token (`settings.py:154`) makes every participant refresh during a 30-minute run. `frontend/e2e/global-setup.ts:48-61` already raises the same policy for e2e. |
| Q-4 | Which LLM providers for personas? | **Confirmed 2026-10-22:** OpenAI and Anthropic, through a small provider interface over plain HTTP, no SDK dependency. | Covers the platform's main providers; `frontend/package.json` carries no LLM SDK today. |
| Q-5 | Where does the tool live and in what language? | **Confirmed 2026-10-22:** `frontend/sim/`, TypeScript, using the pinned `@playwright/test` 1.63.0 as a library, outside the e2e `testDir`. | Reuses the e2e harness's login lessons and the installed Chromium; keeps it out of the `frontend-e2e` CI job. |
| Q-6 | Where do simulated accounts come from? | **Confirmed 2026-10-22:** the tool provisions them through the admin API and reuses them by a fixed email prefix. | Repeated runs need no manual preparation; only admin credentials are required. |
| Q-7 | Should this depend on an open dossier? | **Decided by the spec author, approved by the user 2026-10-22:** no. | The six open dossiers (`docs/tasks/BOARD.md`) edit backend contexts, pack JSON and `src/` components; this task adds `frontend/sim/` and touches only `vite.config.ts`, `tsconfig.json` and `.gitignore` in regions none of them edit. Phase 2's AC-20 is a consumer, not a dependency. |
| Q-8 | How are scenarios written? | **Decided by the spec author, approved by the user 2026-10-22:** as TypeScript modules exporting a typed scenario object, validated with `zod` at load. | Type-checked, allows comments, and adds no dependency (`zod` is already at `frontend/package.json:62`); YAML would add a parser. |
| Q-9 | May the app source gain `data-testid`s for the simulator? | **Decided by the spec author, approved by the user 2026-10-22:** not in this task. | Adding them would edit `ChatroomComposer.vue`, `ActivityPanel.vue` and the bubble component, which phase 3 also edits. Selectors use accessible names with the locale pinned to `en` (§6.5); testids are FU-2. |

## 4. Current State

**No multi-participant tooling exists.** The e2e suite has two accounts only, `e2e-user` and
`e2e-admin` (`frontend/e2e/fixtures/auth.ts:12-20`), created at startup by
`backend/app/bootstrap/seed.py:87-102` when `SMAP_APP_ENV=test`, which staging is not. No bulk
seeder or test-only endpoint exists under `backend/app`. Its `baseURL` is fixed at
`http://localhost:5173` (`frontend/playwright.config.ts:18`), with `/api` proxied to
`localhost:28000` (`frontend/vite.config.ts:49-52`); no spec targets staging.

**Isolation precedent.** `frontend/e2e/26-guest-session-lifecycle.spec.ts:77-101` and
`27-guest-sender-marking.spec.ts:112-145` drive several `browser.newContext()` contexts in one
test, which is the identity model this tool scales up. Only the refresh token is a cookie
(`smap_refresh`, path `/api/auth`, `backend/app/api/v1/auth.py:45-59`); the access token lives in
SPA memory (`auth.ts:85-99`), and a reused storage state revokes the token family because the
refresh token rotates (`auth.ts:22-30`). Every context must log in through the form. A guest's
`browser_id` comes from the context's `localStorage`
(`frontend/src/slices/conversation/api/index.ts:598-602`), and a repeated id resumes the same
session (`backend/contexts/conversation/application/guest_session_service.py:180-219`), so a
fresh context per guest gives a fresh guest.

**Provisioning path, all over HTTP.**

- `POST /api/admin/users` creates a `PENDING` account with no password and returns a set-password
  link and a verify-email link (`backend/app/api/v1/admin_users.py:136`, `:64-80`), at most 60 per
  10 minutes per admin (`backend/contexts/identity/application/admin_service.py:68-69`), subject to
  the email-domain policy (`admin_service.py:178-179`).
- Login needs both a password and a verified email (`auth_service.py:277-282`, `:333-334`). The
  tokens are in the link fragments and are spent through `POST /api/auth/reset-password`
  (`auth.py:440`) and `POST /api/auth/verify-email` (`auth.py:319`).
- Project membership exists only through invites: `POST /api/projects/{id}/invites`
  (`projects.py:496`) returns an `accept_url`, accepted by the logged-in, verified invitee through
  `POST /api/invites/accept-by-token` (`invites.py:116`, `:129-132`).
- Member Groups: `POST /api/projects/{id}/member-groups` (`member_groups.py:134`) and
  `POST /api/member-groups/{id}/members` (`:243`); the target must already be a project member
  (`member_group_service.py:188-189`).
- Room: `POST /api/workspaces/{id}/chatrooms` (`chatrooms.py:445`, `allow_member_groups` and
  `allow_project_members` mutually exclusive), agent binding `POST /api/chatrooms/{id}/agents`
  (`:825`, observer binding creator-only `:855-858`), activity grant
  `PATCH .../agents/{agent_id}/activity-control` (`:970`, creator only `:998`), Member Group
  binding `PUT /api/chatrooms/{id}/member-groups` (`:661`), guest link
  `GET /api/chatrooms/{id}/guest-link` returning `/g/{room}/{token}` (`:1331`, `:1368-1372`).

**Participant actions in the UI.** The composer has no testids: its textarea has the accessible
name "Type a message..." and the send button "Send" (`ChatroomComposer.vue:29-36`, `:72-78`).
Messages are `li.bubble-row` with `span.bubble__sender` inside `ol[role=log]`
(`ChatroomMessageBubble.vue:37-61`, `ChatroomView.vue:83-90`); system rows are `li.sys`. The
typing indicator is `p.typing` (`ChatroomTypingIndicator.vue:2-7`). The Activity tab is
`role=tab` named "Activity", present only for the creator or during an activation
(`ChatroomView.vue:877-880`), and inside a drawer below desktop width (`:395-441`). Schema forms
label each field from the type schema (`SchemaForm.vue:126-134`) with a "Submit" or "Propose to
your group" button (`:196-205`). Group proposal cards carry testids for status, approve,
reject and withdraw (`GroupProposalCard.vue:94-187`). A guest cannot vote
(`ActivityPanel.vue:455`). Sending a message is REST only
(`POST /api/chatrooms/{id}/messages`, `messages.py:174`); the chatroom socket carries only
typing and draft frames (`backend/app/api/ws/chatroom.py:212-256`).

**Limits a single-IP class meets.** AUTH 10 per minute per IP covering login, refresh, ws-ticket
and all guest routes; CHAT 60 per minute per user; five sockets per user
(`settings.py:418-428`). Login lockout after 20 failures per IP in 15 minutes
(`backend/contexts/identity/infrastructure/lockouts.py:20-24`). nginx `auth` zone 5 r/s burst 10
on `/api/auth/` (`deploy/compose/nginx/conf.d/smap.conf:122`); `/ws/` has no `limit_req`.
`PATCH /api/admin/rate-limits/{key}` updates the policy row, audits
`admin.rate_limit_patched`, and mirrors it into Redis after commit
(`backend/app/api/v1/admin_rate_limits.py:78-129`).

**Evidence over HTTP.** `GET /api/admin/audit` filters by exact `action`, `resource_type`,
`resource_id` and time, with no room filter (`admin_audit.py:61`,
`backend/contexts/audit/infrastructure/repositories.py:50-51`). Wake-up rows are
`resource_type="agent"` with the room in `metadata.room_id`
(`backend/app/workers/tasks/orchestration.py:183-199`). Proposals are readable by the room
creator through `GET /api/chatrooms/{id}/activity-proposals?activation_id=` (`activities.py:1280`,
`:1287-1306`). The transcript is `GET /api/chatrooms/{id}/messages` with cursors, limit 200
(`messages.py:145-171`). Worker logs have no HTTP surface.

**Workstation-tool precedent.** `deploy/scripts/verify-actor-ip.py` runs from the operator's
machine against `--base-url`, takes the admin password from a flag, `SMAP_ADMIN_PASSWORD` or a
prompt (`:119-129`), and reads its evidence from the admin audit API (`:19-20`).

**Runtime.** Node 22.18 (the development host) and 24.20 (CI, `.github/workflows/ci.yml:23`) run
`.ts` files by type stripping without a loader (verified on 2026-10-22 with `node t.ts`). Type
stripping accepts erasable syntax only: no `enum`, no parameter properties, no namespaces.

## 5. Design

### Options considered

**Option A: browser per participant, everything through the UI.** Realistic presence, typing
and rendering, but provisioning through forms is slow and fragile and evidence would be scraped.

**Option B: HTTP and WebSocket clients only, no browser.** Light and fast, but it bypasses the
SPA that real students use, cannot show the operator the room, and would re-implement the token
refresh, socket ticket and guest flows in the tool.

**Option C (hybrid): browsers for participants, HTTP for the operator's own work.** Each
participant is a Playwright context that logs in and acts through the UI, so presence, typing,
the activity panel and the frontend's own refresh logic are exercised as in class. Setup,
rate-limit control and evidence use the admin and facilitator APIs with tokens the tool obtains
for itself.

### Decision

Option C. The simulator exists to reproduce what a classroom produces in the room, and that
includes the frontend paths (presence, typing frames, the activity panel), so participants must
be browsers. Setup and evidence are operator work in which the UI adds nothing but fragility.
Given up: the tool cannot scale far past classroom size (each context is a renderer process),
which is a non-goal.

**Architecture.** One process, one Chromium, one context per browser-driven actor.

- `config/` loads a target file (`frontend/sim/targets/<name>.json`, gitignored except
  `local.example.json` and `staging.example.json`): base URL, allowed host, admin email,
  facilitator email, simulated-account email prefix and domain, project and workspace ids, and
  `allowRateLimitChange`.
- `scenario/` defines the scenario type and its `zod` schema. A scenario names the room (create
  or existing), actors and steps.
- `actors/` implements four actor kinds: `script` (steps only), `llm` (persona only), `hybrid`
  (persona with steps that pre-empt it) and `human` (the operator's own browser; the tool opens
  nothing for it but provisions it and records its messages through the transcript).
- `actions/` holds one module per action, each taking a Playwright `Page` and the step
  arguments.
- `scheduler/` runs every actor's steps against one run clock. A step has `at` (offset from
  start), `when` (a room condition) or both (the condition armed at `at`), a `timeout`, and
  `onTimeout: "skip" | "abort"`. Room conditions are evaluated against a shared observation of
  the room built from the facilitator's page DOM (§6.5).
- `llm/` holds the provider interface, the OpenAI and Anthropic adapters and the persona loop.
- `setup/` provisions accounts and the room; `limits/` raises and restores the AUTH policy;
  `evidence/` collects and writes the run directory.
- `cli.ts` is the entry: `node sim/cli.ts <command> --target <name> [--scenario <file>]`, with
  commands `provision`, `run`, `dry-run`, `restore-limits` and `collect --room <id> --from --to`.

**Persona loop.** On each new room message, after the actor's `minIntervalSec` and a jittered
reading delay, the persona sends the provider a system prompt (the persona text, the language,
the rule that silence is a valid answer) and the recent transcript as seen in its own page, and
asks for a JSON reply `{ "action": "say" | "silent" | "type_pause", "text"?: string }`. `say`
types the text into the composer at a human typing rate and sends it; `type_pause` types part of
it and stops; `silent` does nothing. A per-actor cap on turns and a per-run cap on calls and
output tokens are hard stops: an exhausted actor turns silent and the event is logged. A
scripted step due for a `hybrid` actor cancels its pending persona reply.

**Rate-limit guard.** Before the first login, `limits/` reads the `auth` policy, writes it to
`frontend/sim/.state/<target>.json`, then patches `max_count` to the scenario's need (default
600 per minute). Restoration runs in a `finally` block and on `SIGINT`/`SIGTERM`. At the start of
every command the tool checks the state file; a leftover entry (a killed process) is restored
before anything else and reported. `restore-limits` does only that.

**Passwords.** Simulated passwords are derived, not stored: HMAC-SHA256 of the account email
under `SMAP_SIM_SECRET`, encoded to 24 characters. Re-running `provision` with the same secret
logs in to existing accounts; a lost secret is recovered by re-issuing activation links
(`admin_users.py:160`).

## 6. Detailed Changes

### 6.1 Backend, API contract, migration

None. `gen:api` is not rerun. No migration.

### 6.2 Files added under `frontend/sim/`

`cli.ts`, `config/`, `scenario/`, `actors/`, `actions/`, `scheduler/`, `llm/`, `setup/`,
`limits/`, `evidence/`, `scenarios/` (shipped scenarios), `targets/*.example.json`,
`__tests__/`, `README.md` and `tsconfig.json` (Node types, `erasableSyntaxOnly`, strict).

Shipped scenarios:

- `scenarios/smoke-local.ts`: two registered students, one guest and a scripted facilitator in
  a new local room without agents. It exercises every action of §6.4 in under five minutes and
  needs no LLM key; it is the tool's own end-to-end check.
- `scenarios/crat-ac20.ts`: the AC-20 protocol, one step per row of its §2 table: P1 to P3
  registered in one Member Group, P4 a guest named like P1, the facilitator scripted (or `human`
  through `--facilitator human`), TA and SA bound normal, AA observer, TA's grant on the CRAT
  type only. The minute-15 "confirm when TA asks" row is a `when` step on the next TA message
  matching the confirmation pattern.
- `scenarios/crat-llm-class.ts`: the same room with P1 to P3 as `hybrid` personas (an eager
  student, a hesitant one, one who drifts off topic) and P4 `script` silent, for free-discussion
  runs.

### 6.3 Setup (`setup/`)

`provision` is idempotent, keyed by email `<prefix>-<n>@<domain>`:

1. As admin, look the account up; create it if absent and spend both activation tokens with the
   derived password.
2. As the project owner (the facilitator account), invite each simulated account that is not
   yet a member; each account logs in and accepts by token.
3. Create or reuse the Member Group named in the scenario and add the members not yet in it.

`room.create` (as facilitator) creates the room with `allow_member_groups` or
`allow_project_members` per the scenario, binds agents by name and role, sets the grant to the
named activity type ids, binds the Member Group and enables guest links. `room.existing` takes
an id and verifies the bindings, grant and guest-link setting the scenario requires, failing
before any participant connects. Every created resource id is written to the run directory.

### 6.4 Actions (`actions/`)

| Action | Implementation |
|---|---|
| `connect` | Login through the form (`fixtures/auth.ts` approach: wait for hydration, read the `/api/auth/login` response, honour `Retry-After`), or guest landing with a display name; wait for `form.composer`. Logins are spaced at least 400 ms apart for the nginx `auth` zone. |
| `say` | Fill the composer by `pressSequentially` at a configurable characters-per-second rate, click Send, wait for the own bubble. |
| `typePause` | Type part of a text and stop for a duration, leaving the draft unsent. |
| `silent` | Mark the actor as present but non-speaking for the window (a scenario assertion for the report). |
| `leave` / late `connect` | Close or open the context at the step time. |
| `openActivity` | Click the tab named "Activity"; the viewport is fixed at 1440 px so the tab is in the rail. |
| `submit` | Fill fields by key: the tool fetches the activity type schema as facilitator and maps keys to labels, then submits. |
| `propose`, `approve`, `reject`, `withdraw` | The schema form in group mode, then the `group-proposal-*` testids. |
| `startActivity`, `endActivity` | Facilitator only, through the panel's select and buttons. |
| `releaseObservation` | Facilitator only, the existing release control. |

### 6.5 Room observation and selectors

Each context sets `localStorage['smap-locale'] = 'en'`
(`frontend/src/shared/composables/useLocale.ts:4`) before navigation, so accessible names match
the `en` catalogue. Room conditions and persona transcripts read `li.bubble-row`
(`span.bubble__sender`, the bubble text, the guest badge) and `li.sys` from the page DOM, so
reading the room costs no API call and no AUTH budget. Every selector lives in one
`actions/selectors.ts`, so a markup change is one edit.

### 6.6 Evidence (`evidence/`)

The run directory is `frontend/sim/runs/<UTC timestamp>-<scenario>/` (gitignored):

- `events.jsonl`: one line per step and persona decision: actor, action, planned and actual
  time, outcome, error.
- `transcript.json`: every room message in the run window, through the facilitator's
  `GET /api/chatrooms/{id}/messages`.
- `audit.json`: `wakeup.fired`, `wakeup.skipped` and `wakeup.failed` rows for each bound agent in
  the window (`GET /api/admin/audit` per action and agent), filtered to `metadata.room_id`.
- `proposals.json`: proposals polled every 15 seconds during the run through the facilitator's
  list endpoint and merged by id, because the endpoint may list live proposals only (OQ-1).
- `report.md`: the scenario timeline with planned and actual times; wake counts per trigger; every
  `rate_limited` skip; persona usage (calls, tokens); restored limits; and the manual worker-log
  command from `ac20-live-run-protocol.md:47`, which has no HTTP surface.
- Playwright traces per context on failure; screenshots on step failure.

`collect` rebuilds `transcript.json`, `audit.json` and `report.md` for a room and window without
running a scenario, for a class run by people.

### 6.7 Repository wiring

- `frontend/vite.config.ts` test `include` gains `sim/**/__tests__/**/*.test.ts`.
- `frontend/tsconfig.json` references `sim/tsconfig.json`, so `pnpm typecheck` covers it.
- `.gitignore` gains `frontend/sim/runs/`, `frontend/sim/.state/` and `frontend/sim/targets/*.json`
  with `!frontend/sim/targets/*.example.json`.
- `package.json` gains `"sim": "node sim/cli.ts"`.
- `docs/examples/chinese-character-courses-adoption.md` gains a short pointer to
  `frontend/sim/README.md` under its dry-run section (`:170-177`).

## 7. NFR Checklist

- [x] i18n: N/A. No user-facing product string; the tool's CLI output is English operator text.
- [x] Audit log: the tool adds no event. Its rate-limit change is audited by the existing
  `admin.rate_limit_patched` (`admin_rate_limits.py:110-120`), and account creation, invites and
  room changes by their existing events, so a run is visible in the audit log.
- [x] Tenant isolation: N/A for new endpoints (none). The tool acts only through the public API
  with the roles the server already checks.
- [x] Error handling UX: every step outcome is recorded; a `when` timeout follows `onTimeout`; a
  failed setup aborts before any participant connects; the rate-limit restore runs on every exit
  path.
- [x] Performance: up to ten Chromium contexts (about 150 to 250 MB each headless, OQ-2). Room
  observation polls the facilitator's API (D-1); proposals are polled every 5 seconds (D-2).

## 8. Security Considerations

- **Credentials.** Admin and facilitator passwords come from `SMAP_SIM_ADMIN_PASSWORD` and
  `SMAP_SIM_FACILITATOR_PASSWORD` or a hidden prompt; LLM keys from `OPENAI_API_KEY` and
  `ANTHROPIC_API_KEY`; the derivation secret from `SMAP_SIM_SECRET`. None is written to a target
  file, the state file, the run directory or the console. The logger redacts any configured
  secret value and every `Authorization` header and token field before writing; no Playwright
  trace is recorded (D-4). CLAUDE.md "Never log
  API keys, tokens, or passwords" applies.
- **Blast radius of the raised limit.** For the run, the AUTH bucket of the whole target is
  raised, which weakens brute-force protection for every user. Mitigations: the change requires
  `allowRateLimitChange: true` in the target file (absent in the staging example; the operator
  sets it knowingly); the raise is bounded (default 600, maximum 2000 per minute); the per-account
  lockout (`lockouts.py:20-24`) is unaffected; restore runs on every exit and on the next start.
- **Production guard.** The base URL's host must equal the target's `allowedHost`, and the CLI
  has no default target. No production example ships.
- **Persona content.** Persona prompts and model output are posted into a room as ordinary user
  messages through the same validated endpoint as a person's; the tool adds no trust. Model output
  is truncated to the composer's limit and never executed or interpreted.
- **Simulated accounts** are ordinary members with no admin role, confined to the configured
  project; the email domain must pass the target's email-domain policy ([R19a.13]), which the
  operator configures rather than the tool bypassing.

## 9. Quality Notes

- **Existing debt.** e2e helpers are duplicated across specs (`26-...spec.ts:26-56`,
  `27-...spec.ts:51-84`); the chat surfaces lack testids, so selectors depend on classes
  (FU-2). The simulator does not import from `e2e/` specs; it copies the login approach of
  `fixtures/auth.ts` into `actions/connect.ts` with a citation, and does not refactor `e2e/`.
- **Patterns to follow.** Operator-tool CLI conventions from
  `deploy/scripts/verify-actor-ip.py` (required target, password from env or prompt, exit codes
  0 pass, 1 failure, 2 error). Login robustness from `frontend/e2e/fixtures/auth.ts:45-110`.
  Erasable TypeScript only (§4 Runtime). One module per action; actions never reach into the
  scheduler.
- **Reuse inventory.** `@playwright/test` 1.63.0 (library use of `chromium`), `zod` 3.25.76,
  Node `fetch`, `crypto.createHmac`, `readline` for hidden prompts. Server-side: the endpoints
  cited in §4; nothing new.

## 10. Risks and Rollback

- **R-1, rate limit left raised.** Covered by the state file, the restore on every exit and on
  the next start, and `restore-limits` (AC-4). Residual: a machine that never runs the tool again
  leaves it raised; the report and README say so and the audit row names the change.
- **R-2, selector drift.** A UI change breaks a step. One selector module and the local smoke
  scenario detect it before a staging run.
- **R-3, LLM cost.** Hard budgets per actor and per run (AC-10); the report prints usage.
- **R-4, scheduling jitter.** Steps fire on one event loop with ten browsers; AC-7 bounds the
  error locally, and `events.jsonl` records the real time so evidence never claims the planned one.
- **R-5, email-domain policy on staging.** Provisioning fails clearly if the simulated domain is
  not allowed; the README states the policy prerequisite.
- **Rollback.** Delete `frontend/sim/` and the wiring lines. No server state changes besides the
  simulated accounts, memberships, rooms and audit rows a run creates, which are ordinary data.

## 11. Acceptance Criteria

Setup and safety

- [x] AC-1: `provision` on a local stack creates the configured number of simulated accounts as
  verified project members in the named Member Group; a second run creates no account, invite or
  membership and succeeds (verified by the admin user list and the group's member list).
- [x] AC-2: After a run, no file under `frontend/sim/runs/`, `.state/` or `targets/`, and no
  console line, contains any configured password, `SMAP_SIM_SECRET`, an LLM key, an access token
  or a refresh token (scripted search of the run output for those values, plus a logger unit
  test).
- [x] AC-3: `run` refuses to start when the base URL host differs from `allowedHost`, and refuses
  to raise the AUTH policy when `allowRateLimitChange` is not `true` (unit tests on config).
- [x] AC-4: After a normal end, a `SIGINT` during a run, and an exception thrown inside a step,
  `GET /api/admin/rate-limits` shows the `auth` policy equal to its pre-run value; with a leftover
  state file from a killed process, the next command restores it first and reports it (local
  stack).
- [x] AC-5: `room.create` produces a room with the scenario's agents, roles, grant, Member Group
  and guest links; `room.existing` with a missing binding fails before any participant connects.

Participants and scheduling

- [x] AC-6: In `smoke-local`, two registered students, one guest named like one of them, and the
  facilitator appear as four distinct presence entries; each message is attributed to its own
  sender; the guest's bubbles carry the guest badge.
- [x] AC-7: On the local stack, `at` steps fire within 2 seconds of their planned offset, and two
  steps planned 5 seconds apart fire inside a 10-second window; `events.jsonl` records planned and
  actual times for both.
- [x] AC-8: A `when` step fires after the condition is observed; a step whose condition is never
  met records `timeout` and the run continues under `skip` or stops under `abort`.
- [x] AC-9: `smoke-local` exercises every action of §6.4 with a recorded success outcome, and
  `typePause` makes another context show the typing indicator without a message being created.

LLM personas

- [x] AC-10: A persona replies through its own composer, may choose silence, respects
  `minIntervalSec`, and stops (silent, logged) when its turn cap or the run's call or token cap is
  reached (unit tests with a stub provider).
- [ ] AC-11: The OpenAI and Anthropic adapters build the documented request shape and parse a
  `say`, a `silent` and a malformed reply (unit tests against recorded fixtures); one live call per
  provider succeeds with an operator key (manual, recorded in §15).
- [x] AC-12: For a `hybrid` actor, a due script step cancels a pending persona reply, and the
  persona resumes after the step.
- [x] AC-13: A `human` actor gets no browser context, is provisioned, and its messages appear in
  `transcript.json`.

Evidence and scenarios

- [x] AC-14: After a run, the run directory holds `events.jsonl`, `transcript.json`, `audit.json`
  (wake-up rows of the room's agents only), `proposals.json` (including proposals that ended
  during the run) and `report.md` with the planned and actual timeline, wake counts per trigger,
  `rate_limited` skips, persona usage, the restored limit and the manual worker-log command.
- [x] AC-15: `collect --room --from --to` rebuilds the transcript, audit and report for a room
  run by people.
- [x] AC-16: `dry-run` of `crat-ac20` prints one scheduled step per row of
  `ac20-live-run-protocol.md` §2 without connecting, and `crat-ac20` declares the protocol's
  preconditions (three registered members in one group, one same-name guest, TA and SA normal, AA
  observer, CRAT-only grant).
- [x] AC-17: `--headed` opens one window per browser actor; the default is headless.

Repository and live

- [x] AC-18: `pnpm typecheck`, `pnpm lint` and `pnpm test` cover `frontend/sim/` and pass in CI;
  the `frontend-e2e` job's test count is unchanged (the simulator is not in `testDir`).
- [ ] AC-19: One run of `crat-ac20` on staging completes with every participant connected for
  the 30 minutes, the AUTH policy restored, and a run directory that the phase 2 dossier's D-n
  entry for AC-20 can cite. Judging AC-20 itself stays in that dossier.

## 12. Test Plan

- **Unit (`frontend/sim/__tests__/`, vitest, CI):** config and guards (AC-3); scenario schema
  and scheduler with fake timers, including `when`, timeouts and pre-emption (AC-7 logic, AC-8,
  AC-12); persona loop with a stub provider (AC-10); adapters against recorded fixtures (AC-11);
  redacting logger (AC-2); limits state file and restore paths with a fake admin client (AC-4
  logic); evidence assembly from canned API pages, including proposal merging (AC-14 logic).
- **Local end-to-end (manual, compose base plus override, recorded in §15):** `provision` twice
  (AC-1), `smoke-local` headless and headed (AC-5, AC-6, AC-7, AC-9, AC-13, AC-17), the three
  restore paths (AC-4), a secret search of the run output (AC-2), `collect` (AC-15), `dry-run`
  of `crat-ac20` (AC-16).
- **Live (manual):** one adapter call per provider (AC-11), the staging run (AC-19).
- **CI:** AC-18 through the existing jobs.

## 13. SRS Delta

None. The simulator is operator tooling outside the product; it uses existing endpoints under
their existing requirements and changes no system behaviour. [R19.04] (rate-limit policy
administration) already covers the policy change it makes.

## 14. Open Questions

- **OQ-1 (resolved, D-2):** The list endpoint returns open proposals only
  (`proposal_repo.py:192`). The observer re-fetches by id any proposal that leaves the list, so
  settled proposals reach `proposals.json` and `when` conditions.
- **OQ-2 (resolved):** On the Windows operator machine, a five-participant `smoke-local` run used
  about 830 to 890 MB of Chromium private memory across eight processes (about 170 MB per
  participant) and 126 MB for Node. The README states the figure and a 2.5 GB budget for ten.
- **OQ-3 (resolved, D-5):** Anthropic personas default to `claude-haiku-5-5`; OpenAI has no
  default and the scenario or `SMAP_SIM_OPENAI_MODEL` must name one. Recorded in the README.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

- **D-1, room observation through the API.** §6.5 planned DOM-based observation. The observer
  instead polls the facilitator's API (latest messages, members, active activation, proposals),
  because the DOM of one participant only shows what that participant can see and a class
  selector change would silently blind every `when` condition. Browsers are still the only path
  for participant actions, and the typing indicator is still read from another participant's DOM.
- **D-2, settled proposals re-fetched; 5-second polling.** OQ-1 found the list endpoint returns
  open proposals only, so the observer fetches `GET .../activity-proposals/{id}` for any proposal
  that leaves the list. Polling runs every 5 seconds rather than 15, so a `when` on a proposal
  state fires within the 2-second-plus-poll budget a scripted timeline needs.
- **D-3, form fields located by element id.** The schema form renders each control with
  `id=<field key>`, so `fillFields` uses `[id="key"]` rather than accessible names, which are
  localised and may repeat.
- **D-4, no Playwright traces; end-of-run screenshots instead.** §8 described traces with auth
  bodies excluded. Playwright traces cannot exclude request bodies selectively, and a trace would
  carry login passwords and tokens. Each connected participant instead opens the People tab and
  writes `screens/end-<actor>.png` before leaving.
- **D-5, persona model defaults (OQ-3).** Anthropic defaults to `claude-haiku-5-5`. OpenAI has no
  default, because no single model was verified against the JSON reply shape; a scenario or
  `SMAP_SIM_OPENAI_MODEL` must name one.
- **D-6, observation release and personas are not exercised by `smoke-local`.** The local smoke
  scenario has no agents and no LLM key, so `releaseObservation` and `persona` are covered by unit
  tests and by `crat-ac20` / `crat-llm-class` on a stack with agents and keys. AC-9 is ticked for
  the remaining actions.
- **D-7, own ESLint block for `sim/`.** `eslint.config.js` gains a TypeScript-parser block for
  `sim/**/*.ts`, adds it to gate 13's file list, and ignores `sim/runs/**` and `sim/.state/**`.
- **D-8, AC-20 scenario detail.** In `crat-ac20` the first proposal is rejected by both P1 and P2,
  so the protocol's rejection path is visible in `proposals.json` with two votes; the scenario
  notes record this.
- **D-9, simulator unit tests run in jsdom.** The shared `tests/setup.ts` uses DOM globals such as
  `Range`, so a `node` environment for `sim/` would need a second setup file. The tests run in the
  default jsdom environment and use no DOM API themselves.
- **D-10, module layout.** §6.2 named `actions/` and a single evidence module. The build has
  `browser/` (participant and selectors), `observe/`, `session.ts` (login, admin client, AUTH
  guard wiring), `sources.ts` (API adapters behind `RoomSource`, `TranscriptSource` and
  `AuditSource`), `evidence/write.ts` and `lib/time.ts`, after the quality audit split the runner
  and the evidence source interface.
- **D-11, security hardening beyond §8.** The check-security audit led to: the simulated email
  domain must be reserved (`example.*`, `.test`, `.invalid`, `.example`, `.localhost`) unless the
  target sets `simDomainIsOperatorControlled`; non-loopback targets must use https; the AUTH
  restore is compare-and-set against the applied value and the state file names its owner pid, so
  a second run on the same machine is refused and a concurrent change is never overwritten;
  persona output starting with `/` is treated as silence, since the composer would run it as a
  slash command; HTTP redirects are refused; `SMAP_SIM_SECRET` must be at least 16 characters;
  guest-link, invite and activation tokens are registered with the redactor, and every evidence
  file is written through it.
- **D-12, exit codes.** `run` exits 1 only when a step failed or the run aborted. A `when` timeout
  under `skip` is an expected outcome and is reported as "N timed out" without failing the run.
- **D-13, 20-second action timeout.** Playwright used as a library has no default action timeout,
  so a missing element hung a step indefinitely in the first local run. Each context sets 20
  seconds.
- **D-14, SIGINT verified in a Linux container.** Windows cannot deliver SIGINT to a process
  programmatically, so the signal path of AC-4 ran in `node:24-alpine` against the local stack via
  `host.docker.internal`, with the real guard, both with a working restore and with an injected
  restore failure. The self-audit found that a second Ctrl-C during the restore reached Node's
  default handler and killed the process mid-restore; the handlers are now persistent. With
  `--init` (so the process is not PID 1, which ignores default-action signals) and a slowed
  restore, two SIGINTs 300 ms apart left the limit raised before the fix and restored it after.
- **D-15, Playwright signal handlers disabled.** `chromium.launch` sets `handleSIGINT`,
  `handleSIGTERM` and `handleSIGHUP` to false; Playwright's handlers would otherwise close the
  browser and exit before the AUTH restore ran.
- **D-16, stray rejections are logged, not fatal.** The CLI catches `unhandledRejection` to keep
  library errors redacted. Exiting there (the first version) would skip the AUTH restore of a run
  in progress, so the handler logs and lets the run's own error path finish; `uncaughtException`
  still exits 2 and prints the `restore-limits` command. A rejection injected through `--import`
  during `restore-limits` was logged and the command still completed with exit 0.

### Verification record (2026-10-22, local test stack)

Mechanical gates: `pnpm lint` and `pnpm typecheck` clean (a planted type error under `sim/`
confirmed typecheck covers it); 79 simulator unit tests pass. The full `pnpm test` and the
`frontend-e2e` count are left to CI (AC-18).

`provision` run twice produced three active, verified simulated accounts, four project members
and one group of three, with no duplicate on the second run (database check, AC-1). `smoke-local`
ran headless and headed: every step succeeded except its deliberate timeout, `at` steps fired
within 0.1 seconds of plan, `typing.observed` recorded `visible: true` with no message created,
`proposals.json` held the withdrawn and the accepted proposal, and the guest named "Sim Amy" was
attributed as a guest with its own id and badge; the People tab showed both "Sim Amy" entries
(AC-6 to AC-9, AC-14, AC-17). A search of the run directory and console for both passwords, the
secret, the derived passwords, `eyJ` and the guest token found nothing, while the room id as a
positive control matched 15 times (AC-2). A human facilitator got no browser and its API-posted
message reached the transcript and satisfied a student's `when` (AC-13). The AUTH policy returned
to its pre-run value after a normal end and after a failing step; after `Stop-Process -Force` the
policy stayed raised with the state file present, and `restore-limits` restored it and removed
the file; SIGINT restored and exited 130, and with an injected failure printed the recovery
command and exited 2 (AC-4, D-14). `collect` rebuilt a ten-message room (AC-15). An existing room
without guest links failed before any participant connected, and a created room carried TA and SA
as normal agents with the CRAT-only grant, AA as observer, the group binding and guest links
(AC-5); the wake audit captured the fake provider's `wakeup.failed`. `dry-run` of `crat-ac20`
printed the protocol timeline without connecting (AC-16).

CI run 39812881866 on `025a42ae`: every frontend and backend job passed, including
`frontend-test`, `frontend-lint`, `frontend-typecheck` and `frontend-e2e` (AC-18). `gate` was red only
through `dependency-audit`, which flagged newly published `pypdf 6.19.0` advisories unrelated to
this task.

Still open: AC-11's live call per provider needs an operator key; AC-19 is the operator's
staging run.

## 16. Follow-ups

- **FU-1:** An LLM judge that reads `report.md` and the transcript against a protocol's pass
  criteria and drafts the dossier's D-n entry.
- **FU-2:** `data-testid`s on the composer, bubbles, typing indicator and activity panel, after
  `2026-10-20-crat-facilitation-phase3-late-joiners-and-activity-state` lands, to replace the
  class selectors in `actions/selectors.ts`.
- **FU-3:** A server-side room filter on `GET /api/admin/audit` (`metadata.room_id`), which would
  remove the client-side filtering in `evidence/`.
- **FU-4:** A `deprovision` command (or account rotation) that deactivates the simulated accounts
  and removes their memberships, so a target can be cleaned without the admin UI.
- **FU-5:** Two operators running the simulator against one target from two machines are not
  coordinated: the state file is per machine, so the second run finds the limit already raised,
  leaves it alone, and loses the raise when the first run restores while it is still connecting
  participants. A server-side marker (for example a
  note on the policy) would close this; the README documents the residual.
- **FU-6:** Persona live calls are summarised by usage counts in `report.md`, but the prompts and
  raw model replies are not kept. A redacted per-call log would make persona behaviour reviewable
  after a run.
