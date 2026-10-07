---
type: bugfix
status: in-progress
created: 2026-10-07
requirements: [R13.19, R13.27]
depends_on: []
---

# Stop agent replies from disappearing on clients

## 1. Summary

A participant in a busy room can watch an agent's reply stream in and then vanish: the
streaming bubble is cleared and no message takes its place until the page is reloaded, the
socket reconnects or the tab regains focus, and even then the reply appears above messages
that were sent while it was being written. It affects every viewer, members and guests
alike, and is most likely in a class where students keep posting while an agent replies.
The cause is that an agent reply is timestamped when its database transaction began (the
start of the provider stream), not when it was written, while every client fetches new
messages with a "newer than the last one I saw" cursor; any message seen during the stream
moves the cursor past the reply. Three smaller client races drop messages the same way.

## 2. Observed vs Expected

- **Observed**:
  - `messages.created_at` defaults to `now()`
    (`backend/contexts/conversation/infrastructure/tables.py:266`), which in Postgres is the
    start time of the current transaction. `MessageRepository.create` sets no timestamp
    (`backend/contexts/conversation/infrastructure/repositories/message_repo.py:52-64`).
  - The turn engine commits its pre-stream writes and starts streaming
    (`backend/contexts/agents/application/runtime/turn_engine.py:3056-3063`); the router's
    first read at stream start (`backend/contexts/keys/application/provider_router.py:461`)
    opens the transaction in which the reply is later inserted and committed
    (`turn_engine.py:3239-3250`). The reply's `created_at` is therefore the start of the
    stream, often tens of seconds before it exists.
  - On `message.created` the client only replays a delta `since=lastSeenMessageId`
    (`frontend/src/slices/conversation/composables/useChatroomSocket.ts:122-143, 356-359`);
    the server returns rows strictly after the cursor by `(created_at, id)`
    (`message_repo.py:138-162`); the cursor follows the newest `created_at` in the cache
    (`useChatroomSocket.ts:739-759`) and every applied delta row (`:282-293`). A message
    committed during the stream moves the cursor past the back-dated reply, so its delta
    returns nothing. `agent.finished` then clears the streaming bubble unconditionally
    (`:492-502`).
  - The messages query's fetch function reads the cache only when the page has arrived and
    merges with `mergeMessages` (`frontend/src/slices/conversation/composables/useChatroomMessages.ts:98-105`),
    which drops any cached row inside the page's window that the page lacks
    (`frontend/src/slices/conversation/utils/mergeMessages.ts:21-35`); a reply applied live
    while that fetch was in flight is removed again.
  - A newer delta invalidates an older one that is still in flight
    (`useChatroomSocket.ts:130-134`), so the older delta's rows are discarded even when the
    newer one starts after a cursor the older one had not yet covered.
  - A reconnect reconciles only the newest `PAGE_SIZE` (100) messages
    (`useChatroomSocket.ts:303-320, 646-659`; `useChatroomMessages.ts:35`), and the merge keeps
    older cached rows, so more than 100 messages missed during one gap leave a hole the
    cursor has already jumped past.
- **Expected** — clients learn of each new message over `WSS /ws/chatroom/{id}` ([R13.19])
  and render it; once the reply row commits, the turn's outcome is a fact (`turn_engine.py:3250-3255`,
  [R13.27]) and every viewer should see it. The streaming bubble's own comment assumes the
  persisted message "has already arrived via message.created" when it is cleared
  (`useChatroomSocket.ts:496-500`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How does the server stamp `created_at`? | A migration changes the `messages.created_at` server default to `clock_timestamp()`, the time of the insert statement itself. | Requester's choice. Every writer benefits and stored order matches when replies actually land. Committing right before the reply insert was rejected as narrower (any other long transaction keeps the defect); an application-side timestamp was rejected because API and worker clocks may disagree. A sub-second gap between insert and commit remains; the client fix in Q-2 covers it. |
| Q-2 | How does the client stop losing a reply it was told about? | On `message.created`, also fetch the named message by id (`getMessage`, as `message.updated` already does, `frontend/src/slices/conversation/views/ChatroomView.vue:1206`) and apply it; `agent.finished` keeps the streaming bubble until its `message_id` is in the cache or a fetch of it fails. | Requester's choice. It does not depend on timestamps at all and also covers a lost delta and a lost `message.created` publish (`turn_engine.py:3271-3287`: the two frames are published independently and a failure is only logged). Reconciling on an empty delta was rejected as still clearing the bubble early. |
| Q-3 | Which secondary causes are in scope? | All three: the refetch merge race, the delta generation race, and backfilling gaps over 100 messages. | Requester's choice. Each loses a whole message with the same symptom. |
| Q-4 | Does this depend on another dossier? | `depends_on: []`. | `2026-10-07-room-roster-completeness` also edits `useChatroomSocket.ts`, but its presence and typing handlers, not the message, delta, reconcile or `agent.finished` paths touched here; no shared lines. Migration numbering: `0100` and `0101` are claimed by approved, unbuilt dossiers (`2026-10-07-google-display-name-normalisation`, `2026-10-07-display-name-validation`); this one takes the next free revision at build time. |

## 4. Reproduction

1. A room with one bound agent and two participants, A and B, both with the room open.
2. A asks a question that makes the agent stream for several seconds (a long answer or a
   tool round).
3. While the reply is streaming, B posts any message. Both clients apply B's message and
   move their cursor to it.
4. The agent finishes. On both clients the streaming bubble disappears and no reply
   appears. Switching tabs and back (focus refetch) or reloading shows the reply, placed
   above B's message.
5. Deterministic backend check: in a db-tier test, open a session and run a query, then in
   a second session insert and commit message S; insert message R in the first session and
   commit; R's `created_at` is earlier than S's.

## 5. Root Cause Analysis

1. **Root cause:** the `messages.created_at` default is the transaction start time
   (`tables.py:266`), and the reply's transaction starts at the beginning of the stream
   (`provider_router.py:461`, `turn_engine.py:3061-3063, 3239-3250`), so the reply is
   back-dated by the length of the stream.
2. **Root cause on the client:** a reply the client was explicitly told about
   (`message.created` carries `message_id`) is fetched only through a timestamp cursor
   (`useChatroomSocket.ts:132`), so any back-dating, or a missed frame, loses it; and
   `agent.finished` clears the stream without checking the reply arrived (`:501`).
3. Contributing client races: the query fetch function merges against a stale snapshot
   (`useChatroomMessages.ts:100-103` with `mergeMessages.ts:31`); a newer delta discards an
   older one's rows (`useChatroomSocket.ts:130-134`); reconnect reads only the newest page
   (`:311`).

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every viewer of every room with an agent, whenever another message is
  committed while an agent streams; observer agents are unaffected (their output is an
  observation, not a message). Rows already stored keep their back-dated `created_at`: the
  true insert time was never recorded, so no data repair is possible; history ordering of
  past replies stays as it is.
- **Sibling suspects**:
  - Other tables with `server_default=now()` written in long transactions:
    `tables.py:27, 51, 165, 217, 238` in conversation; only `messages` feeds a timestamp
    cursor that clients poll with — the others are ordered or filtered server-side only.
    Recorded as FU-1 for a wider review.
  - User sends: the POST's transaction is short, but the same cursor logic applies;
    covered by the id-based fetch.
  - System messages (released observations, activity echoes) are written in short request
    transactions; covered by the same changes.
  - Slow-consumer closes (`backend/shared_kernel/realtime/connection.py:380-384`) and other
    socket drops are recovered by the reconnect reconcile, which this dossier makes
    gap-complete.

## 7. Fix Design

1. **Server timestamp.** Migration: `ALTER TABLE messages ALTER COLUMN created_at SET
   DEFAULT clock_timestamp()`; `tables.py:266` mirrors it (`server_default=sa.text("clock_timestamp()")`).
   Downgrade restores `now()`.
2. **Fetch the named message.** The `message.created` handler keeps the since-delta and also
   calls `getMessage(ev.message_id)` and applies the row through `applyMessageCreated`
   (dedupe by id is already there, `useChatroomSocket.ts:287`). The cursor keeps following
   the newest `created_at` (`:739-759`), so applying an out-of-order row never moves it
   backwards — `applyMessageCreated` stops setting `lastSeenMessageId` to the applied row
   unconditionally (`:291`) and leaves the cursor to the cache subscription.
3. **Hold the bubble.** `agent.finished` with a `message_id` clears the agent's stream once
   that id is in the cache, fetching it by id if needed, and clears it anyway if the fetch
   fails or the frame carries no `message_id` (error and empty-reply turns), keeping today's
   cleanup for those.
4. **Refetch merge.** The query's fetch function merges the page with the cache as it is at
   resolve time but keeps rows the cache gained after the request started (record the
   request start, keep cached rows whose ids were applied since), so an in-flight refetch
   cannot delete a live arrival. `reconcileMessages` gets the same rule.
5. **Delta race.** A delta's rows are applied whether or not a newer delta started; the
   dedupe by id makes this safe. The generation counter is kept only to stop a stale
   *reconcile page* from overwriting newer data.
6. **Gap backfill.** After the reconnect reconcile, if the cache's previous newest row is not
   in the returned page, page forward with `since` from that row (`limit` 200, the server
   maximum, `backend/app/api/v1/messages.py:146-150`) until a page comes back short.

## 8. Regression Test Plan

Written first; each fails against current code for the stated reason.

- `backend/tests/integration/test_message_created_at_db.py` (db tier): two sessions; the
  first runs a query, the second inserts and commits S, the first then inserts R and
  commits; assert `R.created_at > S.created_at`. Fails today because R is stamped with the
  first session's transaction start.
- `frontend/src/slices/conversation/__tests__/useChatroomSocket.test.ts`:
  - `message.created` for R, whose since-delta returns `[]` because the cursor is already at
    S, ends with R in the cache (served by the by-id fetch). Fails today: R never arrives.
  - `agent.finished` for R arriving before R is in the cache keeps the streaming bubble
    until R is applied. Fails today: the stream is cleared at once.
  - Two deltas in flight, the older resolving last: its rows are still applied. Fails today:
    they are discarded.
  - A reconnect after 150 missed messages fills the gap (the cache holds all 150). Fails
    today: 50 remain missing.
- `frontend/src/slices/conversation/__tests__/useChatroomMessages.test.ts`: a row applied to
  the cache while the query's refetch is in flight survives the refetch's merge. Fails
  today: the merge drops it.

## 9. Risks and Rollback

- `clock_timestamp()` changes the stored time of messages inserted inside longer
  transactions to their insert time; nothing reads `created_at` as a transaction marker.
- One extra `GET /api/messages/{id}` per `message.created` per viewer; small, and cached
  rows short-circuit through the dedupe.
- Backfill after a long gap issues several page requests; bounded by the missed volume.
- Rollback: revert the client; downgrade restores `now()`. Rows written meanwhile keep
  their insert-time stamps, which old code reads without issue.

## 10. Acceptance Criteria

- [ ] AC-1: the regression tests in §8 fail before the fix and pass after.
- [ ] AC-2: an agent reply committed while other messages are posted during its stream
  appears on every connected client without reload, focus change or reconnect, and is
  ordered after the messages committed before it.
- [x] AC-3: the streaming bubble is cleared only once the reply is shown, or when the turn
  produced no message.
- [x] AC-4: a message applied live is never removed by a concurrent refetch or reconcile.
- [x] AC-5: after a reconnect, every message created during the gap is in the cache,
  however many there were.

Verification status at build close (2026-10-07). AC-3 is pinned by the `useChatroomSocket`
cases "keeps the streaming bubble until the finished reply is in the cache", "clears the
streaming bubble at once when the reply is already cached", "clears the streaming bubble
when the finished reply cannot be fetched" and "does not let a failed fetch of a finished
reply clear the next turn", plus the existing no-`message_id` cases. AC-4 is pinned by the
`useChatroomMessages` refetch-merge case and the socket case "keeps a row applied live
while the reconnect page was in flight" (observed failing with the merge rule removed).
AC-5 is pinned by "fills a reconnect gap larger than one page" and "still fills the gap
when a row of the reconcile page was deleted meanwhile". For AC-1, the five frontend
regression tests were observed failing on `0cac6d3d` for the documented reasons and passing
after; the db-tier test could not run on the build host (no Postgres), so its fail-first
and pass are owed to CI. AC-2 and AC-6 need CI (db tier, e2e) and a running stack; neither
was available on the build host.
- [ ] AC-6: the migration upgrades and downgrades cleanly; backend and frontend lint,
  typecheck, tests (db tier included), build and e2e pass in CI.

## 11. SRS Delta

None. [R13.19] already requires clients to receive new messages.

## 12. Deviation Log

Appended by /build.

- **D-1.** The migration is `0102_messages_created_at_clock_timestamp`, revising `0099`.
  Requester's choice at plan approval: `0100` and `0101` stay with the two display-name
  dossiers that claim them, and whichever builds next takes its `down_revision` from
  `alembic heads`. A scratch-database test of the migration in both directions was added to
  `test_message_created_at_db.py` for AC-6.
- **D-2.** §7.6 pages the gap forward with `since` from the previous newest row. The build
  pages backwards with `before` from the reconcile page's oldest row, `limit` 200, until it
  reaches a row at or before the newest row shown before the gap or a page comes back short.
  That row may itself have been deleted during the gap, and a dead `since` anchor is a 422;
  the backwards walk does not need it to exist. The gap is detected on the page's raw length,
  before tombstoned rows are filtered.
- **D-3.** §7.4's "record the request start, keep cached rows whose ids were applied since"
  is implemented as the set of ids cached when the request started (`mergeMessages`'
  `knownAtRequest`); a cached row outside that set is kept. Same rule, no timestamps.
- **D-4.** Beyond §7, three small additions on the same paths. The reconcile page drops
  tombstoned rows, so a message deleted while the page was in flight is not resurrected. A
  row fetched by id is applied only when its `chatroom_id` is this room (security-gate
  hardening; the frame's `message_id` is server-authored today). The by-id fetch is skipped
  when the row is already cached, and concurrent requests for one id share a fetch.

## 13. Follow-ups

- **FU-1.** Other `server_default=now()` timestamps written inside long transactions
  (conversation `tables.py:27, 51, 165, 217, 238`, and other contexts) are stamped at
  transaction start too; none feeds a client cursor today, but a review would find any that
  should be `clock_timestamp()`.
- **FU-2.** Replies stored before the migration keep their back-dated `created_at` and sort
  above messages posted during their stream; there is no record of their true insert time.
- **FU-3.** The `scratch_engine` fixture now exists in three db-tier files
  (`test_guest_kick_and_ban_db.py`, `test_migration_0084_schema.py`,
  `test_message_created_at_db.py`); hoist it into `tests/integration/conftest.py`. Deferred
  from the quality gate because it edits two unrelated test files that cannot be run on a
  host without Postgres.
- **FU-4.** `useChatroomSocket.ts` is past 800 lines; the message-delivery functions
  (delta, by-id fetch, reconcile, backfill, bubble hold) are a separable composable.
- **FU-5.** `clearAgentSideEffects` resets an agent's streaming draft whenever any row from
  that agent arrives, so a late row from a finished turn (delta, reconcile page, by-id
  fetch, backfill) can clear the start of the agent's next turn. Pre-existing on the delta
  and reconcile paths.
- **FU-6.** Each viewer now reads a new message twice (since-delta and by-id); the by-id read
  is skipped only when the delta resolved first. Cheap today; revisit if room fan-out
  makes it visible.
