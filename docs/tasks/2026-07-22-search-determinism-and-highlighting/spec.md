---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R13.18, R24.41, R24.42]
depends_on: []
---

# Chatroom search returns a page it cannot reproduce, and highlights it in a tag nobody agreed on

## 1. Summary

Two confirmed defects in chatroom full-text search, from two audits:

- **F-22** (`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:569-582`) — the
  snippet highlight marker exists in three incompatible forms. The backend emits
  PostgreSQL's default `<b>`, the sanitiser allowlists `b` but not `mark`, and both the CSS
  and the UI spec are written against `<mark>`.
- **V-6** (`docs/audits/2026-07-22-conversation-verification-gap/findings.md:259-289`) — the
  search query orders by `rank` alone, a key that is non-unique by construction, under
  `LIMIT`/`OFFSET`. The same query re-run can return a different result set.

**They do not share a root cause.** F-22 is a presentation-contract drift: three
independently-authored layers each picked a highlight markup and nothing forces them to
agree. V-6 is a query-correctness defect: a sort key that is not a total order combined with
a row-count cut. Neither causes nor aggravates the other, and either could be fixed alone.

**They are grouped by change surface**, which is this repo's stated grouping rule
(`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:664-666`). Both fixes land
inside the *same 45-line method* —
`backend/contexts/conversation/infrastructure/repositories/message_repo.py:226-270`,
`MessageRepository.search` — F-22 at the `ts_headline` options (`:247-252`) and V-6 at the
`order_by` (`:261`). Both then surface through the same single frontend consumer
(`frontend/src/slices/conversation/composables/useChatroomSearch.ts:19-36` →
`frontend/src/slices/conversation/components/ChatroomSearchPanel.vue:40-58`). A revert of
either would touch the other's diff hunk. Splitting them into two dossiers would produce two
conflicting patches to one function for no benefit.

Scope note required by the grouping rule: **both findings are `confirmed`**, not plausible.
V-6 carries a caveat about its *intent source*, not its mechanism — see §3, Q-1.

## 2. Observed vs Expected

### F-22 — the highlight tag

| Layer | What it does today | Citation |
|---|---|---|
| Backend | Passes `ts_headline` only `MaxWords=35,MinWords=15,ShortWord=3` — no `StartSel`/`StopSel`, so PostgreSQL applies its documented default of `<b>`/`</b>` | `message_repo.py:247-252`, options literal at `:251` |
| Sanitiser | `ALLOWED_TAGS` contains `b` (`:49`); `mark` is absent from the whole list | `frontend/src/slices/conversation/utils/renderMarkdown.ts:46-85` |
| CSS | Styles `.result__snippet :deep(mark)` with `--color-warning-tint` | `ChatroomSearchPanel.vue:165-169` |
| UI spec | "search terms highlighted with `<mark>` tag (yellow background `--color-warning-tint`, `--radius-sm` padding)" | `docs/UI/07-conversation.md:751` |

**Observed.** A search match renders in browser-default bold. The `:deep(mark)` block at
`ChatroomSearchPanel.vue:165-169` never matches an element — it is dead CSS. The design
token it references is defined for both themes (`frontend/src/shared/styles/main.css:32`
light, `:201` dark) and goes unused on this path.

**Expected.** `docs/UI/07-conversation.md:751` — a `<mark>` element carrying the warning
tint.

**The trap the audit flagged, verified.** Correcting only the backend makes highlighting
*disappear entirely*: `sanitizeSnippet` (`renderMarkdown.ts:93-96`) runs DOMPurify with
`PURIFY_CONFIG`, whose `ALLOWED_TAGS` (`:47-81`) omits `mark` and which sets no
`KEEP_CONTENT: false`, so DOMPurify's default keep-content behaviour strips the element and
retains the bare text. The result would be worse than today. Correcting only the frontend
leaves the CSS just as dead. **The two halves must ship in one commit.**

### V-6 — the non-reproducible page

**Observed.** `message_repo.py:261` is `.order_by(sa.desc("rank"))`, the sole ordering key,
with `.limit(limit).offset(offset)` at `:262-263`. `rank` comes from
`sa.func.ts_rank_cd(t.messages.c.content_tsv, tsq)` at `:246` with no normalization
argument. Two messages that each contain one occurrence of the term at the same weight
receive an identical `ts_rank_cd` value. In a chat room where a term recurs, that is the
majority case, not an edge case. `ORDER BY` on a non-unique key under `LIMIT` leaves the
order among equal keys to the plan's input order, so re-running the identical query can
return a different set in a different order.

`offset` is public and accepted up to 10 000 (`backend/app/api/v1/search.py:43`) and is
passed through unmodified (`message_service.py:113-128` →
`message_repo.py:226-233`), advertising a paging contract the ordering cannot honour: with a
non-total order, consecutive `OFFSET` windows can duplicate and skip rows.

**Expected.** For a given room and query, the same request returns the same page. There is
no *documented* ordering contract — `[R13.18]` (`REQUIREMENTS.md:679`) and
`docs/implement/F-chat-realtime.md:226` both stop at "uses `content_tsv` + `ts_rank_cd`;
snippet via `ts_headline`". The expectation is entailed by the exposed `offset` parameter
rather than stated. See Q-1.

**Secondary observation, same site.** With all-equal ranks the result order is arbitrary
rather than chronological, while `ChatroomSearchPanel.vue:40-58` renders hits in server
order with a per-row timestamp at `:50`, and `docs/UI/07-conversation.md:713-723` illustrates
three results ascending by time. This is an illustration, not a contract; it informs the
tiebreak choice in §7 rather than constituting a separate defect.

## 3. Clarifications

| ID | Question | Answer | Rationale |
|---|---|---|---|
| Q-1 | V-6's hand-off row offers "standalone, or demote to a follow-up per its Intent-source caveat — the user's call at spec time" (`docs/audits/2026-07-22-conversation-verification-gap/findings.md:477`). Fix or demote? | **Fix, in this dossier.** | The originating audit's own hand-off table already answered this by routing V-6 here alongside F-22 (`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:679`), and the retention dossier at `docs/tasks/2026-07-22-retention-sweep-fixes/spec.md` §7.3 has already committed to fixing the *identical shape* elsewhere in the same codebase, citing V-6 by name as its precedent. Demoting the case that is cited as precedent while fixing the case that cites it is incoherent. The missing intent source is addressed by pinning the order in a test (§8, T-1) rather than by adding an SRS line (§11). |
| Q-2 | Ordering key: minimal `rank DESC, id DESC`, or `rank DESC, created_at DESC, id DESC`? | **`rank DESC, created_at DESC, id DESC`.** | `id` alone makes the order total — that is all determinism strictly requires. But `messages.id` is `gen_random_uuid()` (`backend/contexts/conversation/infrastructure/tables.py:130`), a random v4 uncorrelated with time; with it as the only tiebreak the all-equal-rank case degrades to a *stable but meaningless* shuffle, which the panel then renders with visible per-row timestamps (`ChatroomSearchPanel.vue:50`). Inserting `created_at DESC` costs one comparison and makes the common case read chronologically, matching the illustration at `docs/UI/07-conversation.md:713-723`. `id` stays as the final key because `created_at` defaults to `now()` (`tables.py:150`), which is transaction time and therefore genuinely tie-able. |
| Q-3 | Add `mark` to the shared `PURIFY_CONFIG`, or give snippets their own config? | **A derived snippet config.** | `PURIFY_CONFIG` (`renderMarkdown.ts:46-85`) governs message bodies, which is a reviewed security artifact under `[R24.41]` (`REQUIREMENTS.md:1940`). Widening it also permits raw `<mark>` in message markdown — markdown-it runs with `html: true` (`renderMarkdown.ts:29`) — which nothing asked for. Define `SNIPPET_CONFIG` in the same module as `{ ...PURIFY_CONFIG, ALLOWED_TAGS: [...] }` so it *derives* from the reviewed config and cannot drift on `ALLOWED_ATTR`, `FORBID_ATTR` or `FORBID_TAGS`. One sanitiser module is preserved, which is what `[R24.41]` and the ESLint gate at `frontend/eslint.config.js:229-252` actually require — not one config object. |
| Q-4 | Also add a `ts_rank_cd` normalization argument to reduce tie frequency? | **No.** | Normalization changes the rank *distribution*; it cannot guarantee uniqueness, so it does not make the order total and does not fix V-6. It would also silently change relevance ranking for every existing query — a behaviour change smuggled into a bugfix. Recorded as FU-4. |
| Q-5 | Expose `offset` in the frontend client as part of this fix? | **No.** | `searchMessages` (`frontend/src/slices/conversation/api/index.ts:327-333`) takes only `chatroomId`, `q`, `limit`; no caller can page today, which is what currently bounds V-6's blast radius. Adding paging is a feature. This dossier makes the existing `offset` *safe*; FU-1 records what deep paging would additionally need. |
| Q-6 | `depends_on` — empty? | **Yes, `[]`.** | Positive claim, checked two ways. **Logical:** neither fix references code another dossier introduces; both edit lines that exist today. **Overlap:** the only other non-`implemented` dossier citing `message_repo.py` is `docs/tasks/2026-07-22-reconnect-reconciliation/spec.md`, and every citation is to `list`/`hard_delete` at `:87-145` and `:219-224`; its §7 fix design is frontend-only (`useChatroomSocket.ts`). No dossier under `docs/tasks/` proposes an edit to `renderMarkdown.ts` — the only non-`implemented` mentions are `chat-export-authz-and-polling`, `compaction-scoping-and-durability` and `turn-idempotency-and-locking`, none of which match `renderMarkdown|ALLOWED_TAGS|PURIFY`, and `2026-07-16-code-editor-syntax-highlighting` (`status: implemented`) cites `renderMarkdown.ts:113` only as a lazy-import exemplar. `2026-07-22-retention-sweep-fixes` is the *conceptual* precedent for V-6 but shares no file: it edits `retention_service.py` and `retention.py`. |

## 4. Reproduction

### F-22 (deterministic, no infra beyond a running stack)

1. Post a message containing the word `revenue` into a chatroom.
2. Open the search panel and search `revenue`.
3. Inspect the rendered snippet element (`ChatroomSearchPanel.vue:52-56`, the `v-html`
   binding). The match is wrapped in `<b>`, not `<mark>`; computed background on the match is
   the panel background, not `--color-warning-tint`.

Pure-frontend form, no stack needed: call
`sanitizeSnippet('a <mark>b</mark> c')` from `renderMarkdown.ts:93-96` and observe the
output is `a b c` — the element is gone, the text kept. This is the half that makes a
backend-only fix a regression.

### V-6 (requires Postgres; `pytest.mark.db` tier)

1. Insert 300 messages into one room, each containing exactly one occurrence of `revenue`
   and otherwise distinct text, so `ts_rank_cd` (`message_repo.py:246`) returns an identical
   value for all 300.
2. Call `MessageRepository.search(chatroom_id=..., query="revenue", limit=50)` twice, forcing
   a different plan between the runs (toggle `enable_seqscan` / `enable_bitmapscan`, or run
   `VACUUM FULL` on `messages` between calls to rewrite heap order).
3. Compare the two returned id lists. They differ in membership, not merely in order.

The plan-dependence is the defect, so step 2 is the honest way to expose it; T-1 in §8 is
the plan-independent companion assertion for CI.

## 5. Root Cause Analysis

### F-22 — one cause

**Root cause:** `MessageRepository.search` relies on PostgreSQL's *default* `StartSel`/
`StopSel` because its `ts_headline` options string omits them
(`message_repo.py:251` passes only `MaxWords=35,MinWords=15,ShortWord=3`), while every
downstream consumer was written against the `<mark>` specified at
`docs/UI/07-conversation.md:751`. The marker format is a cross-layer contract that no layer
declares and no test pins.

**Not causes — aggravating factors:**

- *The shared sanitiser config.* `sanitizeSnippet` reuses `PURIFY_CONFIG`
  (`renderMarkdown.ts:95`), so a backend-only correction would delete the tag rather than
  render it. This makes the defect **fix-resistant**; it did not create it. Had `mark` been
  allowlisted from the start, the backend would still have been emitting the wrong tag.
- *No test on the pipeline.* No file under `frontend/src` references `sanitizeSnippet` outside
  the four production sites (`renderMarkdown.ts:94`, `useChatroomSearch.ts:9,21`,
  `ChatroomSearchPanel.vue:52`), and no test file references it at all; the only search
  coverage anywhere is transport-shaped
  (`frontend/src/slices/conversation/api/__tests__/index.spec.ts:393-398`). Absent coverage
  let the drift persist. It did not introduce it.
- *The CSS being written first.* `ChatroomSearchPanel.vue:165-169` is faithful to the spec.
  Writing correct CSS against an unimplemented backend contract is not a defect.

### V-6 — one cause

**Root cause:** the sort key at `message_repo.py:261` is not a total order. `ORDER BY rank
DESC` with `LIMIT`/`OFFSET` (`:262-263`) leaves the choice among tied rows to the plan, so
the *set* returned — not merely its order — is unspecified.

**Not causes — aggravating factors:**

- *The missing `ts_rank_cd` normalization at `:246`.* This raises tie **frequency** from
  "occasionally" to "typically". It is a magnitude multiplier. Even a normalized rank is a
  float over a finite domain and would still tie; the query would still be non-deterministic,
  just less often. Fixing normalization alone would mask the defect (§7).
- *The publicly-exposed `offset` up to 10 000* (`backend/app/api/v1/search.py:43`). This
  converts an unreproducible single page into an unsound paging contract — it widens the
  consequence. Removing `offset` would not make the single page reproducible.
- *No ordering test.* `backend/tests/unit/test_message_repo.py` covers only anchor scoping for
  `list` (`:33-92`); nothing asserts anything about `search`.

**Relationship to the retention purge.** `docs/tasks/2026-07-22-retention-sweep-fixes/spec.md`
§7.3 records the same shape at `retention_service.py:52-60` and prescribes an `id` tiebreak,
stating: "`created_at` is not unique, and an `ORDER BY` on a non-unique key with `LIMIT` is
exactly the non-reproducibility recorded as V-6 in the same audit." That reasoning is
**reused here verbatim** — the mechanism is identical, and the remedy is the same class:
append keys until the order is total, ending with the primary key. Two differences worth
stating, neither of which changes the remedy:

1. *Which key is primary is decided by the feature, not by determinism.* Retention orders by
   `created_at` because the operation is semantically "oldest first"; search orders by `rank`
   because the operation is semantically "most relevant first". Determinism is what the
   trailing `id` supplies in both.
2. *Tie probability differs by orders of magnitude.* `created_at` ties only when rows are
   written in one transaction; `rank` ties whenever two messages mention the term the same
   number of times, which is the normal condition of a chat room. Search is therefore the
   more acute instance of the same defect, not a theoretical one.

## 6. Blast Radius and Sibling Suspects

### Blast radius

- **F-22.** Cosmetic, one component. No data path, no security consequence — DOMPurify runs
  on the same input either way. Currently *strictly* cosmetic: the snippet text is complete
  and correct, only its emphasis element is wrong.
- **V-6.** Search only. No data loss, no disclosure, no wrong content — every hit returned is
  a genuine match (the `@@` predicate at `message_repo.py:258` is unaffected). What is broken
  is reproducibility of the returned set, plus the soundness of `offset` paging. Bounded
  further today because no client sends `offset`
  (`frontend/src/slices/conversation/api/index.ts:327-333`), so the duplicate-and-skip half is
  currently unreachable from the UI. It is reachable by any direct API caller.

### Sweep A — `ORDER BY` on a non-unique key with a row-count cut

Every `.order_by(...)` in `backend/contexts/**` that is combined with `.limit(...)` was
checked. Sites whose ordering key is already total, or which have no `LIMIT`, are cleared;
each verdict carries its evidence.

| Site | Ordering key | Verdict |
|---|---|---|
| `conversation/infrastructure/repositories/message_repo.py:261` `search` | `rank` only | **Confirmed — V-6, this dossier.** Ties are the common case, not incidental |
| `conversation/infrastructure/repositories/message_repo.py:147` `list` | composite keyset, `order_by(*order_cols)` with the anchor resolved at `:87-145` | Clear — this is the file's reference keyset, cited as such by `notification/infrastructure/repositories.py:131` |
| `conversation/infrastructure/repositories/message_repo.py:297` `all_for_chatroom` | `created_at ASC`, **no `LIMIT` in the ordering-sensitive sense** — `limit` caps memory for an export that consumes the whole set (`:280-288`) | Clear — a cap on a full dump, not a page |
| `conversation/infrastructure/repositories/observation_repo.py:133,161` | `(created_at DESC, id DESC)` | Clear — already total |
| `notification/infrastructure/repositories.py:116-121` | `(created_at DESC, id DESC)` with a composite keyset cursor | Clear — and its `DATA-PAGINATION` comment at `:106-111` is the prior art for exactly this class of bug |
| `agent_groups/infrastructure/group_repository.py:112` | `(created_at DESC, id DESC)` | Clear — already total |
| `activities/infrastructure/repositories/submission_repo.py:266,303` | `(created_at DESC, id DESC)` | Clear — already total |
| `activities/infrastructure/repositories/type_repo.py:124` | `(created_at DESC, id DESC)` | Clear — already total |
| `knowledge/infrastructure/graphrag_repositories.py:474` | `(created_at, id)` over a union, with `limit`/`offset` | Clear — already total |
| `audit/infrastructure/repositories.py:40` | `id DESC` on the PK, `limit(limit + 1)` | Clear — PK is unique, so the order is total by itself |
| `identity/application/admin_service.py:74` | `id DESC` on the PK | Clear — same reason |
| `skills/infrastructure/repositories.py:119` | `name`, with `limit`/`offset` | Clear — `name` is unique within the scope the query filters on (`:112-113` filters `scope` + `_owner_predicate`), enforced by four partial unique indexes at `backend/alembic/versions/0056_skills.py:125-137`. Total order by constraint. The sibling at `:273` additionally carries `(name, id)` |
| `workflow/infrastructure/repositories.py:104,108` | `created_at DESC` + `limit`/`offset` | Shape present, not filed — `created_at` is `now()`, i.e. transaction time, so ties require two workflows created in one transaction; `insert` at `:112` writes one row per call. Recorded as **FU-5** with the condition stated, not silently cleared |
| `keys/infrastructure/repositories.py:157-159` | `created_at DESC` + `limit`/`offset` | Same as above — one key per upload request. **FU-5** |
| `identity/infrastructure/repositories.py:246-248` | `last_used_at DESC` + `limit`/`offset` | Same shape; `last_used_at` is updated per request, so cross-row ties need same-transaction writes. **FU-5** |
| `agents/infrastructure/repositories.py:213-217` | `created_at DESC` + `offset`, `limit` optional | Same shape. **FU-5** |
| `knowledge/infrastructure/repositories.py:353-355` | `uploaded_at DESC` + `limit`/`offset` | **Not cleared.** The insert at `:214-226` is single-row per call, but it was not verified that no upload route loops it inside one transaction — and if it does, `now()` gives every document in that batch an identical `uploaded_at`. Stated as an open item in **FU-5**, not as a verdict |
| `workflow/infrastructure/repositories.py:504` | `started_at DESC` over a union + `limit`/`offset` | Same shape as the `created_at` group; runs start one at a time. **FU-5** |
| All remaining `.order_by(...)` sites listed in `backend/contexts/**` | — | Clear — no `LIMIT`; they order a complete result set, where a non-total key affects presentation of tied rows but never membership |

**Why FU-5 rather than in scope.** Those sites share V-6's *shape* but not its condition: a
high-cardinality timestamp written once per request ties only under same-transaction batch
writes, whereas `rank` ties by construction. Sweeping them into this dossier would mean
editing eight repositories across six contexts to fix a defect none of them has been shown to
exhibit. The condition under which they *would* is recorded so the next reader can check it.

### Sweep B — highlight markup and sanitisation

| Site | Verdict |
|---|---|
| `ts_headline` callers | One, `message_repo.py:247-252`. Repo-wide grep for `ts_headline` across `backend/` returns only this call site plus the trigger and index in `backend/alembic/versions/0017_messages.py:79-91`, which do not produce markup |
| Callers of `MessageRepository.search` | One, `message_service.py:113-128`, itself called only from `backend/app/api/v1/search.py:54`. No worker, tool, or MCP path consumes snippets |
| `DOMPurify.sanitize` call sites | Three, all in `renderMarkdown.ts`: `:90` (`renderMarkdown`), `:95` (`sanitizeSnippet`), `:161` (Mermaid SVG, its own inline config). No second sanitiser module exists |
| `v-html` bindings | The five-file ESLint allowlist at `frontend/eslint.config.js:229-252`. Only `ChatroomSearchPanel.vue:55` (listed at `:244`) binds a `sanitizeSnippet` product; the other four bind `renderMarkdown` output and are untouched |
| `mark`-targeting CSS | One, `ChatroomSearchPanel.vue:165-169`. Repo-wide grep for `<mark` / `deep(mark)` across `frontend/src` returns no other site — so no other component is silently depending on the current `<b>` |

## 7. Fix Design

Three edits, in two files.

### 7.1 F-22 backend — name the delimiters

`backend/contexts/conversation/infrastructure/repositories/message_repo.py:251` — extend the
`ts_headline` options literal to
`"StartSel=<mark>,StopSel=</mark>,MaxWords=35,MinWords=15,ShortWord=3"`. The existing three
options keep their current values; the change is purely additive. Update the `search`
docstring (`:234-241`), which currently documents the ordering and the `plainto_tsquery`
injection posture but says nothing about the marker, to state that the marker is `<mark>` per
`docs/UI/07-conversation.md:751` and that the frontend allowlist must match.

### 7.2 F-22 frontend — allowlist exactly one more element

`frontend/src/slices/conversation/utils/renderMarkdown.ts` — add, immediately after
`PURIFY_CONFIG` (`:46-85`), a `SNIPPET_CONFIG` that spreads `PURIFY_CONFIG` and appends
`'mark'` to `ALLOWED_TAGS`, and switch `sanitizeSnippet` (`:93-96`) to pass it.
`renderMarkdown` (`:88-91`) keeps `PURIFY_CONFIG` unchanged.

**This fix touches XSS sanitisation, deliberately and minimally.** Stating what must not
weaken, since the project rule is that sanitisation is never bypassed
(`[R24.41]`/`[R24.42]`, `REQUIREMENTS.md:1940-1941`; `frontend/CLAUDE.md`, "Never bypass
DOMPurify sanitization"):

- **The pipeline order is unchanged.** `ts_headline` interpolates markers into *unescaped*
  user text — `content_md` is raw markdown (`tables.py:146`) and PostgreSQL does not
  HTML-escape the surrounding fragment. DOMPurify is therefore the *only* thing standing
  between a user's message and `v-html` at `ChatroomSearchPanel.vue:55`. Sanitisation stays
  strictly after the backend produces the string, on the full string, every time.
- **Two tempting shortcuts are forbidden.** (a) Trusting the snippet because the markers are
  now "ours" — the markers are ours, the text between them is not. (b) Sanitising first and
  re-inserting `<mark>` afterwards by string surgery, which reintroduces raw HTML
  post-sanitiser and violates `[R24.42]`.
- **The delta is one element name.** `ALLOWED_ATTR` (`:82`), `FORBID_ATTR` (`:83`) and
  `FORBID_TAGS` (`:84`) are inherited by spread and must not be edited. `mark` is a
  content-only element with no attribute surface, no URL surface and no scripting surface;
  with `ALLOWED_ATTR` unchanged, a `<mark onclick=...>` from a hostile message is stripped to
  a bare `<mark>` exactly as `<b onclick=...>` is today.
- **The ESLint allowlist is not extended.** `frontend/eslint.config.js:235-248` keeps its five
  files. No new `v-html` binding is introduced.
- **`PURIFY_CONFIG` is not widened**, so message-body rendering — the larger attack surface —
  is bit-for-bit unchanged. This is Q-3's reason for deriving rather than editing.

No CSS change is needed: `ChatroomSearchPanel.vue:165-169` becomes live as written, and
`--color-warning-tint` is defined for both themes (`main.css:32`, `:201`).

### 7.3 V-6 — make the order total

`message_repo.py:261` — replace `.order_by(sa.desc("rank"))` with
`.order_by(sa.desc("rank"), t.messages.c.created_at.desc(), t.messages.c.id.desc())`. Record
in the docstring (`:234-241`) that the trailing `id` exists to make the order total, so a
later reader does not "simplify" it away — the same protective note
`docs/tasks/2026-07-22-retention-sweep-fixes/spec.md` §7.3 prescribes for its site.

No migration and no new index. `rank` is a computed expression, so a sort over the matched
set is already unavoidable; the GIN index (`REQUIREMENTS.md:1334`,
`backend/alembic/versions/0017_messages.py:79-80`) bounds that set via the `@@` predicate at
`message_repo.py:258`, and the two extra keys add comparisons within a set that is already
being sorted in memory. This is where search is *cheaper* to fix than the retention purge,
whose §7.3 had to raise an index question because it was adding an `ORDER BY`
where there had been none.

### Why this corrects rather than masks

**V-6.** The masking fixes are the ones Q-4 and Q-5 reject. Adding `ts_rank_cd` normalization
makes ties rarer, so the bug reproduces less often and looks fixed — but the query remains
non-deterministic and the failure simply becomes harder to catch. Removing or capping
`offset` hides the paging half while leaving a single page unreproducible. Appending keys
until the order is total removes the cause: with `id` (the primary key, `tables.py:130`) as
the final key, no two candidate rows can compare equal, so the plan has no freedom left. The
result is correct for *any* rank distribution, including a future ranking function nobody has
written yet.

**F-22.** The masking fix is to delete the `:deep(mark)` rule and restyle `b`, making the code
self-consistent by lowering it to what it happens to do. That contradicts
`docs/UI/07-conversation.md:751`, keeps a semantically wrong element (`<b>` is presentational
bold; `<mark>` is "relevant to the user's current activity" — precisely a search hit, and the
difference is read aloud by assistive technology), and leaves the same unpinned cross-layer
contract free to drift again. Emitting the specified element and allowlisting exactly it
makes all four layers agree with the one written specification, and T-2/T-4 in §8 then hold
them there.

### Data repair

**None required, for either finding, and none is possible.**

- **V-6.** Search results are computed per request; nothing is persisted. `search` has exactly
  one caller (`backend/app/api/v1/search.py:54`) which serialises directly to the HTTP
  response (`:60-71`). No cache, no materialised view, no stored ranking. The next query after
  deploy is correct; there is no historical artifact carrying a wrong order.
- **F-22.** Snippets are likewise never stored. The export path that *does* persist rendered
  content uses `all_for_chatroom` (`message_repo.py:272-288`, whose docstring names the export
  worker as its consumer) and never calls `search`, so no `<b>`-marked snippet exists in MinIO
  or anywhere else to rewrite.

The only durable artifact this dossier invalidates is documentation-adjacent: the dead CSS
rule at `ChatroomSearchPanel.vue:165-169` stops being dead. That is the fix, not a repair.

## 8. Regression Test Plan

Failing tests first, in this order. `/build` writes each, watches it fail for the stated
reason, then applies the corresponding fix from §7. Tier markers used below are declared at
`backend/pyproject.toml:384-390` (`unit`, `integration`, `db`, `e2e`, `wiring`); the `db` tier
is live, used by five files under `backend/tests/integration/`.

### T-1 — V-6, plan-independent (backend unit) — **first**

**File:** `backend/tests/unit/test_message_repo.py`, new class `TestMessageSearchOrdering`.
**`test_search_order_is_total`** — build a `MessageRepository` over an `AsyncMock` db as the
existing tests do (`:38-45`), call `repo.search(chatroom_id=..., query="x", limit=10)`, take
`db.execute.await_args_list[0].args[0]` and compile it with the postgresql dialect exactly as
`:47-52` does. Assert the compiled SQL contains an `ORDER BY` naming `rank`, `created_at` and
`messages.id`, and that `ORDER BY` precedes `LIMIT`.
**Why it fails today:** `message_repo.py:261` emits `ORDER BY rank DESC` and nothing else, so
neither `created_at` nor `id` appears in the ordering clause. This mirrors the assertion style
already used in this file and the one prescribed in `2026-07-22-retention-sweep-fixes` for the
identical defect.

### T-2 — F-22 backend contract (backend unit)

**File:** same new class or a sibling `TestMessageSearchSnippet` in
`backend/tests/unit/test_message_repo.py`.
**`test_snippet_uses_mark_delimiters`** — same mock-and-compile approach; assert the compiled
SQL contains `StartSel=<mark>` and `StopSel=</mark>`.
**Why it fails today:** the options literal at `message_repo.py:251` is
`MaxWords=35,MinWords=15,ShortWord=3` with no `StartSel`/`StopSel`, so neither substring is
present and PostgreSQL falls back to `<b>`. This test is the executable form of the contract
at `docs/UI/07-conversation.md:751` — the thing whose absence is F-22's root cause.

### T-3 — V-6, behavioural proof (backend integration, `pytest.mark.db`)

**File:** `backend/tests/integration/test_message_search_determinism.py` (new), following
`backend/tests/integration/test_retention_restore_barrier.py` for its `pytestmark =
pytest.mark.db` declaration and id-tracked cleanup helpers.
**`test_identical_ranks_page_reproducibly`** — insert 300 messages into one room, each
containing exactly one occurrence of `revenue`; run `repo.search(..., query="revenue",
limit=50)`, then again after forcing a different plan (toggle `enable_seqscan`, or
`VACUUM FULL messages`), and assert both runs return the same 50 ids in the same sequence.
**Why it fails today:** with `ORDER BY rank DESC` alone (`:261`) and 300 identical ranks, the
returned 50 are whichever the plan yields first; the two runs disagree.
**Fragility stated openly:** today's failure is plan-dependent by construction — that *is* the
defect. T-1 is the plan-independent companion that keeps CI honest, which is why both ship.
This is the same two-test pairing, for the same stated reason, as
`docs/tasks/2026-07-22-retention-sweep-fixes/spec.md` §8 T-1/T-2.
**`test_offset_pages_do_not_overlap`** — same fixture; fetch `limit=50, offset=0` and
`limit=50, offset=50` and assert the two id sets are disjoint and their union has 100 members.
**Why it fails today:** without a total order the two `OFFSET` windows are drawn from
independently-ordered scans, so rows recur across pages and others are never returned.

### T-4 — F-22 frontend, the tag survives (frontend unit)

**File:** `frontend/src/slices/conversation/__tests__/renderMarkdown.test.ts` (new — no test
file anywhere under `frontend/src` currently references `renderMarkdown` or `sanitizeSnippet`;
the tier and directory convention are established by the 20 existing specs under
`frontend/src/slices/conversation/__tests__/`).
**`sanitizeSnippet preserves <mark>`** — assert
`sanitizeSnippet('a <mark>b</mark> c')` contains `<mark>b</mark>`.
**Why it fails today:** `sanitizeSnippet` (`renderMarkdown.ts:95`) runs `PURIFY_CONFIG`, whose
`ALLOWED_TAGS` (`:47-81`) omits `mark`; DOMPurify's default keep-content behaviour returns
`a b c`, so the assertion finds no element.

### T-5 — F-22, the sanitiser did not weaken (frontend unit) — **passes today, must keep passing**

**File:** same.
**`sanitizeSnippet still strips scripts, handlers and styles`** — assert that
`<script>alert(1)</script>`, `<img src=x onerror=alert(1)>`, `<iframe src=...>` and
`<b style="...">` each lose the script/handler/style/frame, table-driven.
**Why it must exist:** T-4 widens an allowlist. This is the guard that the widening was
one element and nothing else, pinning `ALLOWED_ATTR` (`:82`), `FORBID_ATTR` (`:83`) and
`FORBID_TAGS` (`:84`) behaviourally so a later "just add one more tag" edit trips a test.
**`renderMarkdown does not allow <mark>`** — assert `renderMarkdown('<mark>x</mark>')` yields
no `<mark>`, pinning Q-3: the message-body config stays unwidened.

### T-6 — F-22 end of the chain (frontend component unit)

**File:** `frontend/src/slices/conversation/__tests__/ChatroomSearchPanel.test.ts` (new; the
component has no test today — the only search-adjacent coverage in the slice is the transport
assertion at `frontend/src/slices/conversation/api/__tests__/index.spec.ts:393-398`, and
`ChatroomView.test.ts` contains no reference to search or snippets).
**`renders a mark element for a highlighted hit`** — mount the panel with one `SearchHit` and
a `renderedSnippets` map produced by the real `sanitizeSnippet`, then assert
`wrapper.find('mark').exists()`.
**Why it fails today:** the map is built by `useChatroomSearch.ts:21` through `sanitizeSnippet`,
which strips the element, so no `mark` node reaches the DOM at
`ChatroomSearchPanel.vue:55`. This is the only test that exercises backend marker → sanitiser
→ `v-html` → styled element as one chain, which is the chain F-22 broke.

**No e2e test is proposed.** A Playwright assertion on a highlight colour would be the most
expensive possible way to restate T-6, and the `e2e` tier does not currently host any search
scenario.

## 9. Risks and Rollback

| Risk | Assessment |
|---|---|
| `mark` in the snippet allowlist widens XSS surface | Minimal and bounded. `mark` carries no attribute, URL or scripting surface; `ALLOWED_ATTR`/`FORBID_ATTR`/`FORBID_TAGS` are inherited unchanged by spread; T-5 pins that behaviourally. The message-body config is untouched |
| A user's message literally containing `<mark>` now renders highlighted in *snippets* | True and accepted. It is indistinguishable from a real hit visually, and it is the same class of confusion that `<b>` already permits today. Not a security issue; not worth a second escaping layer that would break the real markers |
| The extra sort keys slow search | No measurable cost. The set being sorted is already bounded by the `@@` predicate + GIN index, and the sort is already in memory because `rank` is computed. No index, no migration |
| Result *order* changes for users | Yes, and intentionally: among equal-rank hits the order becomes newest-first instead of arbitrary. There is no prior order to preserve — that is the finding |
| Marker change breaks a consumer we missed | Sweep B established a single producer and a single consumer, both edited here |

**Rollback.** The three edits are independently revertible. §7.1 and §7.2 must revert
*together* — reverting only §7.2 leaves the backend emitting `<mark>` into a sanitiser that
deletes it, which is worse than the pre-fix state (§2). §7.3 is orthogonal to both and can be
reverted alone.

## 10. Acceptance Criteria

- [ ] AC-1: `MessageRepository.search` emits `StartSel=<mark>,StopSel=</mark>` in its
      `ts_headline` options (`message_repo.py:251`), with the other three options unchanged.
- [ ] AC-2: `sanitizeSnippet` preserves `<mark>` and strips every element and attribute it
      strips today; `renderMarkdown` still rejects `<mark>`. `PURIFY_CONFIG`'s
      `ALLOWED_ATTR`, `FORBID_ATTR` and `FORBID_TAGS` are byte-identical to their pre-fix
      values, and `SNIPPET_CONFIG` derives from `PURIFY_CONFIG` rather than restating it.
- [ ] AC-3: `frontend/eslint.config.js:235-248` still lists exactly five files, and no new
      `v-html` binding exists anywhere in the repo.
- [ ] AC-4: A search result renders inside a `<mark>` element carrying
      `--color-warning-tint`; `ChatroomSearchPanel.vue:165-169` is no longer dead CSS.
- [ ] AC-5: `MessageRepository.search` orders by `(rank DESC, created_at DESC, id DESC)`, and
      the reason for the trailing `id` is stated in the docstring.
- [ ] AC-6: The same room + query + `limit` returns an identical id sequence across two runs
      with different query plans (T-3).
- [ ] AC-7: `offset=0` and `offset=50` over a 300-row all-tied result set return disjoint
      pages whose union has 100 distinct members (T-3).
- [ ] AC-8: T-1, T-2, T-4 and T-6 each fail before their corresponding fix and pass after.
- [ ] AC-9: No Alembic migration is added and no index is created.
- [ ] AC-10: `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`, `pnpm test`,
      `pnpm lint`, `pnpm typecheck`, `pnpm build` all pass.

## 11. SRS Delta

Empty. Both fixes restore or complete behaviour already specified elsewhere: F-22 against
`docs/UI/07-conversation.md:751`, and V-6 against the paging contract that
`backend/app/api/v1/search.py:43` already advertises by exposing `offset`.

V-6's finding notes that no SRS ordering contract exists (`[R13.18]`, `REQUIREMENTS.md:679`;
`docs/implement/F-chat-realtime.md:226`). **Deliberately not adding one.** An SRS line that
says "results are deterministically ordered" is unenforceable prose; T-1 is the same statement
in a form that fails when violated. If a future dossier introduces *user-facing* search paging
— see FU-1 — that dossier defines the ordering contract in the SRS, because paging is the
feature that makes ordering a promise to users rather than an implementation invariant.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — `OFFSET` paging over a live table remains skew-prone even with a total order:
  messages inserted between two page fetches shift the window. `backend/app/api/v1/search.py:43`
  permits `offset` to 10 000. If search paging is ever exposed in the UI, it needs the keyset
  pattern this codebase already has twice —
  `conversation/infrastructure/repositories/message_repo.py:87-145` and the composite cursor at
  `notification/infrastructure/repositories.py:113-134`, whose `DATA-PAGINATION` comment at
  `:106-111` documents the exact hazard.
- **FU-2** — `offset` is a dead parameter surface: the API accepts it
  (`backend/app/api/v1/search.py:43`) but the generated-client wrapper cannot send it
  (`frontend/src/slices/conversation/api/index.ts:327-333` takes only `chatroomId`, `q`,
  `limit`). Either wire it or drop it — an accepted-but-unreachable parameter is how V-6's
  blast radius stayed small by accident rather than by design.
- **FU-3** — `sanitizeSnippet` applies the *message-body* allowlist to snippets. A snippet is a
  fragment of markdown **source** (`ts_headline` reads `content_md`, `tables.py:146`), so
  allowing `a`, `img`, `table`, `div`, `span` there renders raw HTML a user typed into their
  message as live markup inside a search result. A snippet-only allowlist of `['mark']` would
  be strictly safer and lose nothing a snippet needs. Out of scope here because narrowing is a
  behaviour change requiring its own review; the derived `SNIPPET_CONFIG` introduced in §7.2 is
  the natural place to do it.
- **FU-4** — `ts_rank_cd` at `message_repo.py:246` takes no normalization argument, so rank does
  not account for document length: a one-word message and a 2 000-word message with one
  occurrence rank identically. That is a relevance-quality question, deliberately separated
  from V-6's determinism question (Q-4).
- **FU-5** — Sweep A left eight paginated sites sharing V-6's shape with a timestamp ordering
  key: `workflow/infrastructure/repositories.py:104,504`, `keys/infrastructure/repositories.py:157`,
  `identity/infrastructure/repositories.py:246`, `agents/infrastructure/repositories.py:213`,
  `knowledge/infrastructure/repositories.py:353`. Each is deterministic **only while** no two
  of its rows are written inside one transaction, since `created_at` defaults to `now()`
  (transaction time). `knowledge/...:353` is the one that could not be cleared — it was not
  verified whether any upload route inserts several `rag_documents` in one transaction. A
  single sweep appending `.id` to all eight would close the class for the cost of eight lines.
- **FU-6** — New observation, outside both audits: `docs/UI/07-conversation.md:742` specifies
  "Debounced search: 300ms after last keystroke". No debounce exists. `SSearchInput` emits
  `search` only on Enter (`frontend/src/shared/ui/SSearchInput.vue:36-40`), and
  `useChatroomSearch.runSearch` (`:25-36`) has no timer. Search-as-you-type was specified and
  never built.
</content>
