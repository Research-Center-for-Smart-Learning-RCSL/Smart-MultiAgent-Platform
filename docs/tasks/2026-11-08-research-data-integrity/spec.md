---
type: feature
status: in-progress
created: 2026-11-08
requirements: [R33.10, R33.02, R30.01, R30.05, R30.23, R30.25, R30.26, R30.27]
depends_on: []
---

# Research data integrity before formal data collection

## 1. Summary

The research export ([R33.10]) is the deliverable a classroom study is analysed from, and
its data must be right before formal collection starts, because a defect in recorded data
cannot be repaired afterwards. This dossier closes the research-export findings of
`docs/audits/2026-10-05-teacher-dashboard-and-research-export/findings.md` that were routed
to two dossiers which were never created (`research-export-delivery`,
`research-export-data-shape`): F-5, F-6, F-10, F-12, F-13, F-14 and F-15, plus
`docs/tasks/2026-10-05-chat-export-guest-narrowing/spec.md` FU-1, which is the same defect
as F-10. It adds the traceability that
`docs/assessments/ai-teacher-phase1-spec-review.md:221` asks for before research data
collection: every submission records the activity-type version it was scored under, and
every version's definition is kept. It also adds the creativity scoring that
`docs/assessments/nstc-undergrad-proposal-fit-assessment.md:215-220` (section 5.2) lists as
missing. Fluency is computed and flexibility, originality and convergence are judged by an
LLM against an owner-written rubric. The scores are written asynchronously, are visible
only to teachers and in the export, and never reach students or agents.

## 2. Goals and Non-goals

**Goals**

- A finished research export can be reached and downloaded for as long as the API says it
  is ready, and never after its file is gone (F-5, F-6).
- An export's outcome reflects the export alone: a notification failure does not fail it
  (F-12), a timeout fails it instead of leaving it `running` (F-14), and a re-run cannot
  produce a second bundle.
- Export authority is checked when the job runs and when the file is downloaded, as well
  as when it is requested (F-13).
- Every participant is identified consistently across all files of a bundle. A guest has
  the same `u:` code in the transcripts as in the submissions, and every row says whether
  its subject is a user, a guest or a group (F-10, guest-narrowing FU-1).
- No full UUID appears anywhere in a bundle, as [R33.10] already requires. Room ids and
  the workspace id are replaced by truncated codes.
- Submissions are delivered as one CSV per activity type with flattened payload columns,
  the `sub_scores_filled` columns, unescaped CJK text, and an encoding Excel opens
  correctly (F-15).
- Every submission records the activity-type version it was validated under, and every
  behavioral definition of every type is kept, so a researcher can reconstruct the exact
  schema, validator and group rules a row was scored under.
- An activity type can opt into creativity scoring. Each submission then gets four
  teacher-only scores: fluency, flexibility, originality and convergence. Each score
  records the model that produced it.

**Non-goals**

- Exporting `validator_config` or the creativity rubric. Both may hold answer keys and are
  owner-confidential ([R30.25]). The revision history is for internal reconstruction, and
  the export carries only `type_version` (Q-6).
- Pack or course revision fields (`2026-10-20-crat-facilitation-phase3-...` FU-4,
  `2026-09-03-observer-ui-defect-sweep` FU-6) (Q-6).
- Re-scoring or re-validating existing submissions. Old submissions get no creativity
  scores, and their `type_version` is filled only where it can be proven (Q-7).
- Showing creativity scores to participants, putting them into agent context or digests,
  or letting them drive any automation such as the watchlist, wake triggers or `is_valid`
  (Q-11).
- Cohort-relative originality (rarity within an activation) (Q-9).
- Inter-rater reliability tooling, human override of LLM scores, and calibration
  workflows. These belong in a follow-up once real scores exist (FU-3).
- The chat export, the skills export and any other export. Their equivalent mechanisms
  are reused, not changed.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | One dossier or several? | One dossier. | User choice. Delivery, data shape, versioning and scoring are built and reverted as separate milestones (section 10) so the single dossier still reverts cleanly. |
| Q-2 | How is a submission traced to its type definition? | `type_version` on the submission plus an immutable `activity_type_revisions` table, written on every version bump. | Only this gives an exact reconstruction. A bare version number cannot say what version 2 was once version 3 overwrote it, and snapshots per activation duplicate the definition many times. |
| Q-3 | How does a user get back to a finished export? | The `export.ready` notification carries a download action, and the export dialog re-attaches to the owner's active or latest job for the workspace. | Mirrors the chat export's re-attach on 409 (`useChatroomExport.ts:134-140`). A full export-history page was judged larger than the problem needs. |
| Q-4 | How are guests identified in transcripts? | The same `u:` code the submissions use, derived from the guest session id, plus a `subject_kind` column/field (`user`, `guest`, `group`). | Keeps existing submission codes stable and makes the two files joinable. A new prefix would have changed codes already handed to researchers. |
| Q-5 | How are flattened payload columns laid out? | One CSV per activity type, `submissions/<type_key>.csv`. | Types have different schemas, so a single file would be very wide with colliding names. One file per type opens directly in SPSS and Excel. |
| Q-6 | What is out of scope? | Exporting `validator_config` or the rubric, and pack or course revisions. | User choice. |
| Q-7 | How are existing submissions' versions filled? | Filled only where provable. A type whose `version` is still 1 was never bumped, so all its submissions are version 1. Every other existing submission gets `NULL`, exported as `unknown`. | Research data must not carry a guessed value. Audit rows could prove more, but retention may have purged them ([R17]), so they are not relied on. |
| Q-8 | How does creativity scoring attach to a submission? | An asynchronous layer. The existing validator still decides `is_valid` synchronously, and a separate worker job writes `judge_scores` and `judge_status`. | Keeps class flow independent of LLM latency. "Everyone has submitted" (`check_all_submitted`) and the facilitator's counts must not wait for a model. |
| Q-9 | How is each dimension computed? | Fluency is the count of non-empty declared fields, computed deterministically. Flexibility, originality and convergence are judged by an LLM on a 0-4 scale against an owner-written rubric, each with a short rationale. | Fluency is a count in the classical definition. Cohort-relative originality needs the whole class first and a rescoring pass, which is left out (section 2). |
| Q-10 | Whose provider key pays for scoring? | The room agent's key. The type's scoring config names one project agent, and the job uses that agent's key group and model in the submission's room. | User choice. The reproducibility cost is mitigated by recording the provider and model on every score (section 5). |
| Q-11 | Who can see creativity scores? | Teachers (dashboard) and the research export only. | Scores shown to students would change their behavior and the study's validity. Scores in agent context would let a judge model's output steer facilitation. |

## 4. Current State

### Export delivery

- **Job state.** The job state is a Redis record `research_export:{job_id}`, not a table
  (`backend/contexts/conversation/application/research_export_service.py:31-32,121-139`).
  The statuses are a plain class with QUEUED, RUNNING, READY and FAILED (`:24-28`). Its TTL
  is 24 h (`:19-21`) and every `_store` re-arms it (`:89,138`).
- **The object outlives the record unpredictably.** The ZIP lands in the shared `exports`
  bucket at `{job_id}/research-data.zip` (`research_export_builder.py:97-108`). That bucket
  has a 1-day lifecycle (`backend/smap/bootstrap/minio_init.py:74,156`), and a daily sweep
  deletes objects older than 24 h (`backend/app/workers/tasks/retention.py:599-651`). So
  the record can say `ready` after the object is gone. The GET mints a 15-minute file
  ticket (`backend/app/api/v1/research_export.py:126-134`,
  `shared_kernel/storage/file_tickets.py:29,91`) without checking that the object exists.
  This is the remainder of F-5; the URL-lifetime half was fixed by commit 267fec3c.
- **Re-attaching (F-6).** The dialog keeps a Download button after `ready`
  (`frontend/src/slices/dashboard/components/ResearchExportButton.vue:199-218`), but
  `readyJobId` lives only in the component (`:90-95`). Polling stops on unmount (`:28-29`)
  or after 60 tries (`:51-80`). The `export.ready` notification has no `action`
  (`frontend/src/slices/notifications/lib/kindConfig.ts:80-85`), and its metadata carries
  only `job_id` and `workspace_id` (`backend/app/workers/tasks/conversation.py:493-499`).
  Nothing in the UI leads back to a job.
- **Notification failure (F-12).** `mark_ready` (`conversation.py:490`) and the
  notification send (`:492-499`) share one `try` whose `except` marks the job failed and
  re-raises (`:500-506`).
- **Authority (F-13).** The POST checks computed ownership (`research_export.py:91`), but
  the worker checks only that its arguments match the stored state
  (`conversation.py:389-397`), and the GET checks only the stored owner id
  (`research_export.py:123-124`).
- **Timeout and retries (F-14).** The task is registered bare (`backend/app/workers/main.py:307`):
  worker-wide `job_timeout=600` (`:380`), arq's default retries, and no `CancelledError`
  handling (`conversation.py:500`). The chat export fixed the same pattern in commit
  db36a577 (`conversation.py:282-289`, `main.py:306`).

### Export data shape

- **The CSV.** `submissions.csv` has ten columns: `subject_code`, `activity_type_key`, the
  full `room_id`, `attempt_no`, `is_valid`, `error_class`, `latency_ms`, `created_at`,
  `payload_json` and `sub_scores_json` (`research_export_builder.py:141-154`). The JSON
  columns use `json.dumps` without `ensure_ascii=False` (`:173-174`), so CJK text is
  escaped. The specified flattened columns and `sub_scores_filled(_fields)`
  (`docs/tasks/2026-09-21-research-data-export/spec.md:133-136`) are absent. Two tests pin
  this non-spec header: `test_columns_match_spec` and `test_submissions_csv`
  (`backend/tests/unit/test_research_export_builder.py:126,160-196`).
- **Identity (F-10).** Transcripts map `agent` senders to agent keys and `human`/`user` to
  `subject_code`. Everything else falls through to the literal sender type
  (`research_export_builder.py:214-219`), and guests are stored as `SenderType.GUEST`
  (`backend/app/api/v1/messages.py:201`). So every guest is `"guest"`, while their
  submissions carry a distinct `u:` code from the guest session id
  (`backend/contexts/activities/infrastructure/tables.py:98-108`). `SubmissionRow` has no
  subject kind (`research_export_builder.py:31-43`).
- **Full UUIDs.** `room_id` is a full UUID in the CSV (`:167`), in `observations.json`
  (`:188`) and as the `transcripts.json` key (`:206`). `manifest.json` carries the full
  `workspace_id` (`:243`). Both contradict [R33.10] ("no ... full UUID appears in the
  output").

### Type versioning

- `activity_types.version` defaults to 1 (`tables.py:43`). A behavioral edit of
  `payload_schema`, `validator_kind`, `validator_config` or `group_config` bumps it
  (`backend/contexts/activities/application/type_service.py:219-224,252`,
  `infrastructure/repositories/type_repo.py:328-329`) and overwrites the row in place
  (`type_repo.py:297-340`). Nothing keeps the previous definition; the audit records only
  `version_bumped` (`type_service.py:266`).
- A behavioral edit is refused while an activation of the type is active (`:229-230`).
  The check is a plain read, not a lock.
- `activity_submissions` has `activity_type_id` but no version (`tables.py:177-221`). The
  insert is at `submission_service.py:289-305`. The latest migration is
  `0107_class_closed_at.py`.

### Validation and LLM calls

- **Validation.** In-process validators run inside the submit request
  (`submission_service.py:244-274`); MCP and webhook validators are recorded PENDING
  (`:275-282`). The PENDING ones are dispatched after commit
  (`backend/app/api/v1/activities.py:1409-1413`) to `validate_activity_submission`
  (`backend/app/workers/tasks/activities.py:134-188`). Write-back is idempotent: it
  updates only while status is `pending` (`submission_repo.py:259-295`). A watchdog sweeps
  rows pending over 900 s to `validation_timeout` (`tasks/activities.py:33,203-258`).
- **sub_scores.** These are participant-visible (`app/api/v1/activities.py:292,565-575`)
  and carry `filled`/`filled_fields` (`backend/app/plugins/activity_validators.py:127,157`).
- **Non-agent LLM calls.** These go through `build_router(db).call(group_id, ProviderRequest)`
  (`backend/contexts/keys/infrastructure/adapters/__init__.py:50`,
  `contexts/keys/application/provider_router.py:76-103,377-443`). The summariser pins
  provider and model from an agent (`contexts/agents/application/runtime/summariser.py:30-79`).
  The triple extractor parses model JSON defensively
  (`contexts/knowledge/infrastructure/triple_extractor.py:125-169`). Nothing in
  `shared_kernel` delimits untrusted text in a prompt.

## 5. Design

### Options considered

**Type traceability.** Option A records a bare version number on each submission.
Option B also keeps an immutable revision row per version. Option C snapshots the
definition onto each activation. A is cheapest but loses every overwritten definition. C
reuses the "no behavioral edit while active" rule but copies the definition per
activation, and it does not cover a submission recorded outside an activation's
definition. B was chosen (Q-2).

**Creativity scoring.** Option A is a new `ValidatorKind.LLM_JUDGE` that decides
`is_valid` and the scores together, on the existing PENDING path. Option B is a
synchronous in-process scorer. Option C is a separate asynchronous scoring layer beside
the unchanged validator. A makes class flow wait on a model, because `check_all_submitted`
fires only on a validated result. B blocks the submit request for up to the router's
60-second quota wait plus a 120-second HTTP timeout per attempt. C was chosen (Q-8).

### Decision

**Delivery.**

- **Re-runs.** The task runs only from QUEUED and refuses any other status.
- **Timeout.** It is registered with an explicit timeout and `max_tries=1`, and on
  `CancelledError` it shields a `mark_failed`, as the chat export does.
- **Authority.** The worker re-asserts that the requester is still a computed Project
  Owner of the workspace's project before it reads anything. The GET re-asserts the same
  instead of comparing the stored owner id. A platform admin keeps access, as today.
- **Notification.** The send moves out of the job's failure path; its failure is logged.
- **Expiry.** The ready record stores `uploaded_at`, and `mark_ready` sets the record's
  expiry to `uploaded_at` plus 23 h. That falls before both the sweep and the lifecycle
  rule can delete the object. The GET also checks that the object exists and returns the
  expired error when it does not.
- **Re-attaching.** A per-owner, per-workspace index key points at the latest job, with
  the same expiry. A POST while that job is QUEUED or RUNNING returns 409 with its id. A
  new `GET /api/exports/research/latest?workspace_id=` returns the latest job for the
  dialog to re-attach to. The `export.ready` notification gets an action that opens the
  dashboard with the job id, and the dialog opens on it.

**Data shape.**

- **Bundle layout.** `manifest.json` gets `schema_version: 2` and drops the full workspace
  id in favour of `workspace_code`. `submissions.csv` is replaced by
  `submissions/<type_key>.csv`, one per activity type present. The file name is the type
  key restricted to `[a-z0-9_-]`, with any collision suffixed.
- **Columns.** Each file has `subject_code`, `subject_kind`, `room_code`, `type_version`,
  `attempt_no`, `is_valid`, `error_class`, `latency_ms`, `created_at`, then one column per
  payload field, then `sub_scores_filled`, `sub_scores_filled_fields`, `judge_status`,
  `fluency`, `flexibility`, `originality`, `convergence`, `judge_model`, and finally
  `payload_json` and `sub_scores_json`.
- **Payload fields.** These are the union of declared properties across the type's
  revisions present in the export, in first-seen order. An array is joined with `"; "`
  and an object stays JSON.
- **Encoding.** Every JSON value is written with `ensure_ascii=False`, and every CSV is
  UTF-8 with a byte-order mark, which Excel needs to detect UTF-8.
- **Codes.** A room becomes `r:<8>` everywhere, following `subject_code.py`.
  `SubmissionRow` gains `subject_kind`. A transcript message from a guest gets
  `subject_code(sender_id)`, which equals the code of their submissions because both use
  the guest session id. Each message also gets `subject_kind`.

**Versioning.**

- **The revisions table.** A new table `activity_type_revisions` keyed
  `(activity_type_id, version)` holds `payload_schema`, `validator_kind`,
  `validator_config`, `group_config`, `creativity_scoring`, `created_at` and
  `created_by`. The repository writes a row on type creation and on every bump, in the
  same transaction. Rows are never updated or deleted while the type row exists.
- **The migration.** It seeds one row per existing type at its current version and adds
  a nullable `activity_submissions.type_version`. The backfill sets it only for
  submissions of types whose `version` is 1 (Q-7).
- **Recording the version.** `_record` reads the type's version with `FOR SHARE` on the
  type row and stores it. The share lock makes a concurrent behavioral edit wait for the
  submission, so the stored version cannot be one that was replaced mid-transaction. The
  revisions table follows [R30.25]: only internal code reads it, and no route exposes it.

**Creativity scoring.**

- **Configuration.** A new nullable behavioral field `activity_types.creativity_scoring`
  holds `{"judge_agent_id": <uuid>, "rubric": {"flexibility": str, "originality": str,
  "convergence": str}}`. Editing it bumps `version` and is recorded in the revision. It is
  owner-confidential like `validator_config`. At write time the agent must belong to the
  type's project, as the MCP validator's `agent_id` must; a platform type cannot carry it.
- **Columns.** `activity_submissions` gains `judge_status` (`pending`, `scored`,
  `skipped`, `error`), `judge_scores` JSONB and `judged_at`.
- **Dispatch.** At submit, a type with scoring records `judge_status=pending`.
  `_dispatch_submission` then enqueues `score_activity_submission` with `max_tries=1` and
  its own timeout.
- **Key resolution.** The job resolves the judge agent's binding in the submission's
  room. If the agent is unbound, the job writes `skipped` with reason `judge_not_bound`.
  Otherwise it calls `build_router(db).call` on the agent's key group with the agent's
  provider and model pinned, as the summariser does. The request uses
  `usage_context="activity_judge"`, temperature 0 where the model supports it, and a
  bounded `max_tokens`.
- **Prompt.** The system prompt states the rubric and says that the participant's
  answer is data, not instructions. The answer is sent as a JSON-encoded block
  (`json.dumps(payload, ensure_ascii=False)`) inside fixed delimiters.
- **Parsing and storage.** The output must be JSON with integer scores 0-4 for the three
  dimensions and a rationale of at most 200 characters each. It is parsed defensively,
  following the triple extractor, and clamped. Fluency is computed by the job from the
  type's declared fields with the same `_is_filled` rule `filled_count` uses. The stored
  `judge_scores` holds the four scores, the rationales, `provider`, `model` and
  `prompt_version`, a constant bumped whenever the prompt text changes.
- **Write-back and timeouts.** Write-back updates only while `judge_status='pending'`. The
  existing watchdog also sweeps scoring left pending past 900 s to `error`.
- **Visibility.** Scores never enter `ActivitySubmissionOut`, the agent digest, the
  activity context provider, the room echo or any room-scoped event.
- **Teacher view.** The dashboard's participants table shows each participant's mean
  per dimension over their latest attempt per activation, for types with scoring. The
  research export carries the per-submission columns above.

What was given up: scoring stops when the judge agent is unbound from the room. The model
can change if the owner reconfigures that agent; every score records its model, which
makes such drift visible but does not prevent it (Q-10).

## 6. Detailed Changes

### Backend: activities context

- `domain/models.py`: `ActivityType.creativity_scoring`, a `JudgeStatus` enum, the
  submission's `type_version`, `judge_status`, `judge_scores` and `judged_at`, and an
  `ActivityTypeRevision` record.
- `infrastructure/tables.py`: the new table and columns.
- `infrastructure/repositories/type_repo.py`:
  - `insert` and `update` write the revision row and carry `creativity_scoring`;
  - `get_version_for_share(type_id)`.
- `infrastructure/repositories/submission_repo.py`:
  - the insert carries `type_version` and `judge_status`;
  - `record_judgement` and `record_judge_error`, both guarded on `pending`;
  - the research-export listing returns `type_version`, `subject_kind` and the judge
    fields;
  - a dashboard aggregate for the per-participant means.
- `application/type_service.py`:
  - `creativity_scoring` joins `behavioral_changed` (`:219-224`);
  - its validation checks the rubric shape, and that the agent is in the project and the
    type is project-scoped.
- `application/submission_service.py`: `_record` stores the version and the initial
  `judge_status`.
- A new `application/creativity_judge.py` holds the prompt, the parse and fluency, as pure
  functions.
- The facade exposes:
  - `record_judgement`;
  - `get_submission_for_judging`;
  - the per-participant means for the dashboard.

### Backend: workers and export

- **New job.** `app/workers/tasks/activities.py` gains `score_activity_submission`. It
  resolves the judge binding through `ConversationFacade` and the agent through
  `AgentsFacade`, and calls the router. The watchdog extends to `judge_status`.
- **Dispatch.** `app/api/v1/activities.py`: `_dispatch_submission` enqueues it.
- **Registration.** `app/workers/main.py` registers `research_data_export` and
  `score_activity_submission` with explicit timeouts and `max_tries=1`.
- **Export job.** `app/workers/tasks/conversation.py`:
  - the QUEUED guard;
  - the authority re-check through the tenancy facade;
  - `CancelledError` handling;
  - the notification outside the failure path, with an action payload.
- **Export state.** `contexts/conversation/application/research_export_service.py`:
  - `uploaded_at` and the computed expiry;
  - a StrEnum status;
  - the owner and workspace index;
  - an active-job claim mirroring `export_service.py:130-170`.
- **Export builder.** `research_export_builder.py`:
  - per-type CSVs and room and workspace codes;
  - guest codes and `subject_kind`;
  - BOM and `ensure_ascii=False`;
  - manifest v2.
- **Codes.** `contexts/activities/domain/subject_code.py` gains `room_code`.
- **Migration.** `0108_activity_type_revisions_and_judging.py` creates the table and
  columns, seeds the revisions and backfills the versions. Its downgrade drops them.

### API contract

- `POST /api/exports/research` may return 409 with `job_id`.
- New: `GET /api/exports/research/latest?workspace_id=`.
- `GET /api/exports/research/{job_id}` may return the expired error for a missing
  object.
- The activity-type in/out models gain `creativity_scoring`, which is owner-only on read,
  like `validator_config`.
- The dashboard participants response gains optional creativity means.
- `pnpm run gen:api` must be re-run.

### Frontend

- `slices/dashboard/components/ResearchExportButton.vue`:
  - re-attaches through `latest` and the 409 job id;
  - opens on a job id from the route;
  - replaces hand polling with `usePolling`.
- `slices/notifications/lib/kindConfig.ts`: an `export.ready` action.
- The dashboard participants table gets creativity columns, shown only when present.
- The activity type form (`slices/activities`) gets a creativity-scoring section: a
  toggle, an agent picker over project agents, and three rubric text areas.
- Every new string is added to both `en.json` and `zh-TW.json`.

### Deploy and config

None. No new env vars or compose changes; the exports bucket lifecycle is unchanged.

## 7. NFR Checklist

- [ ] **i18n.** Every new label, the rubric hints, the dashboard columns and the expired
  and 409 states go through `$t()` in both locales.
- [ ] **Audit log.**
  - `activity_type.updated` metadata gains `version`.
  - The export audit row is written once per job; the QUEUED guard prevents a second.
  - New `activity.judged {submission_id, judge_status, model}`, with no score values in
    the audit row.
- [ ] **Tenant isolation.**
  - `latest` is owner-or-admin, scoped to the caller and workspace, and re-checks
    computed ownership.
  - The judge agent must be in the type's project at write time, and bound to the
    submission's room at run time.
  - The dashboard means use the dashboard's existing admission ([R33.02]).
- [ ] **Error handling UX.**
  - Export expired, export already running (409, re-attached), and judge skipped or
    error, shown as a dash with a tooltip.
  - RFC 7807 codes for the new 409 and the expired error reuse the chat export's.
- [ ] **Performance.**
  - One LLM call per submission attempt; about 30 participants × a few attempts per
    activation.
  - The revisions lookup in the export is one query per type.
  - The dashboard means are one grouped query, bounded like the existing participants
    read.

## 8. Security Considerations

- **Prompt injection (check-security, LLM/agent dimension).** The participant's answer
  enters the judge prompt and can try to raise its own score.
  - The answer is JSON-encoded inside fixed delimiters, and the system prompt names it as
    data.
  - The output is accepted only as integers 0-4 plus a bounded rationale; anything else
    becomes `error` with no score.
  - The scores drive no automation (Q-11), so a successful injection can move a number
    in a research column but cannot act.
  - The rationale is stored as text and rendered escaped (DOMPurify path) on the
    dashboard.
- **Confidentiality ([R30.25]).**
  - `creativity_scoring`, which holds the rubric, and `activity_type_revisions` follow
    `validator_config`'s rules: owner-only on project reads, never on room-scoped
    surfaces, events or the export.
  - The judge prompt carries the rubric, but the output never echoes it, and only the
    scores and rationales are stored.
- **Provider keys and cost.**
  - The cost lands on the judge agent's key group, which the project owner chose.
  - The call goes through the router, so the key group's hourly quotas apply.
  - One call per attempt is bounded by the existing submission rate limit
    (`_ROLLING_WINDOW_SECONDS`, `submission_service.py:60`).
  - `max_tries=1` prevents a retry from multiplying cost.
- **AuthZ re-check (F-13).** Both the worker and the GET re-assert computed ownership,
  closing the demotion window.
- **De-identification.** Room and workspace codes close the full-UUID gap against
  [R33.10]. The rationale must not quote personal data. The prompt tells the model to
  describe the answer, not the participant, and the export carries the rationale only
  under the participant's code.

## 9. Quality Notes

**Existing debt in the touched files (record, don't imitate):**

- `ResearchExportStatus` is a plain class, not a StrEnum
  (`research_export_service.py:24-28`). It is converted as part of this task, since every
  status write is touched anyway.
- `ResearchExportButton.vue` polls by hand (`browser-file-delivery` FU at `:654`). It
  moves to `usePolling` here because re-attach rewrites the same code.
- The spec text in `2026-10-19-teacher-dashboard-redesign/spec.md:154-155` says guests
  post as `user`, which is wrong (`messages.py:201`). That is noted, not edited.
- `_ = BytesIO` placeholder (`conversation.py:510`). Leave it.

**Patterns to follow:**

- **Export state, re-attach and active claim:** the chat export,
  `contexts/conversation/application/export_service.py:30-170` and
  `frontend/src/slices/conversation/composables/useChatroomExport.ts:90-153`.
- **Timeout handling:** `conversation.py:282-289` and `main.py:306`.
- **Async write-back:** `SubmissionService.record_validation`
  (`submission_service.py:375-407`).
- **Non-agent LLM call:** `summariser.py:30-79` for pinning, `triple_extractor.py:125-169`
  for parsing.
- **Config ownership check:** the MCP `agent_id` check in `_validate_validator_config`
  (`type_service.py:416-462`).
- **Layering:** the worker crosses contexts only through facades, as
  `research_data_export` does today.

**Reuse inventory:**

- **Codes:** `subject_code`, `group_subject_code` (`activities/domain/subject_code.py:27-43`).
- **Fluency:** `_is_filled` and `FILLED_FIELDS_SUB_SCORE`
  (`app/plugins/activity_validators.py`, `domain/models.py:569-575`).
- **Router:** `build_router`, `ProviderRequest`, `KeyGroupExhausted`
  (`contexts/keys/...`).
- **Model capabilities:** `chat_model_capabilities` (used by the summariser).
- **Downloads and tickets:** `file_tickets.issue`, `content_disposition`;
  `downloadFresh`, `triggerDownload` (`frontend/src/shared/utils/download.ts`).
- **Polling:** `usePolling`.
- **Ownership:** `assert_computed_project_owner` (`research_export.py:91`).
- **Watchdog:** `activities_watchdog` (`tasks/activities.py:203-258`).
- **Schema properties:** `payload_errors` and schema-walking helpers in
  `contexts/activities/domain`, for the declared-property list.

## 10. Risks and Rollback

**Build milestones.** The build runs as four milestones, each its own PR:

1. Delivery: F-5, F-6, F-12, F-13 and F-14.
2. Data shape: F-10, F-15, codes and manifest v2.
3. Versioning: the migration, the revisions table and `type_version`.
4. Creativity scoring.

Milestone 2 leaves the `type_version` and judge columns out of the export until 3 and 4
add them. That keeps each milestone revertible on its own.

**Risks:**

- **Bundle layout change.** Researchers with scripts against `submissions.csv` see a new
  layout. `schema_version: 2` in the manifest names the change, and no formal collection
  has started.
- **Lock contention.** The `FOR SHARE` lock on the type row adds contention between a
  submit and a behavioral edit, which [R30.23] already forbids while active. So the lock
  only orders a race that should not occur.
- **Judge cost and latency** fall on the owner's key. Scoring is opt-in per type, and a
  quota or key failure ends as `error` without affecting the submission.

**Migration rollback.** Migration 0108 is additive. Its downgrade drops the table and the
columns, and existing code ignores the new columns, so rolling back the code before the
schema is safe. Downgrading after scoring has run discards scores and revision history.
That is stated in the release note.

## 11. Acceptance Criteria

**Delivery**

- [x] AC-1: A ready export whose object has been deleted returns the expired error from
  `GET /api/exports/research/{job_id}`, never `ready` with a link. The record's expiry is
  `uploaded_at + 23 h` (unit test with a fake clock and a fake object store).
- [x] AC-2: After the dialog closes or polling gives up, reopening the dialog re-attaches
  to the running or ready job and offers Download. Clicking the `export.ready`
  notification opens it on that job (component test plus one Playwright check).
- [x] AC-3: A POST while the owner's export for the workspace is queued or running
  returns 409 with that job's id, and the dialog continues it.
- [x] AC-4: A notification send failure after `mark_ready` leaves the job `ready` and
  writes one audit row (worker unit test).
- [x] AC-5: A requester demoted from Project Owner after the POST gets a failed job with
  no bundle, and a demoted owner's GET is refused (worker test plus route test).
- [x] AC-6: A job cancelled by timeout reads `failed` ("timed out"). The task is
  registered with `max_tries=1`. Starting it from any status other than QUEUED does
  nothing and writes no audit row.

**Data shape**

- [x] AC-7: In a bundle from a room with two guests and one registered user, each guest
  has a distinct `u:` code. That code is identical across `transcripts.json` and their
  submission CSV rows, and `subject_kind` reads `guest`, `user` and `group` correctly
  (builder test plus a db-tier test through the real listing).
- [x] AC-8: No 36-character UUID appears in any file of a bundle. Rooms read `r:<8>`, and
  the manifest has `workspace_code` and `schema_version: 2` (test greps the unpacked ZIP).
- [x] AC-9: Each activity type in the export has its own `submissions/<key>.csv`, with the
  columns of section 5 in that order. A payload `{"answer":"林"}` appears as `林` both in
  the flattened column and in `payload_json`, and the file starts with a UTF-8 BOM. The
  two old header tests are replaced, not deleted.

**Versioning**

- [x] AC-10: Every behavioral edit writes a revision row equal to the new definition.
  Creation writes version 1. A metadata-only edit writes none (db-tier test).
- [x] AC-11: A submission records its type's version at insert. A behavioral edit
  committed concurrently cannot change the version a submission records (db-tier test
  with two sessions).
- [x] AC-12: After migration 0108 on a database holding a version-1 type and a version-3
  type with submissions:
  - every type has a revision at its current version;
  - version-1 submissions read 1;
  - the others read `NULL` and export as `unknown`.

  The downgrade then removes all of it (migration test).
- [x] AC-13: No HTTP route, room event or export file contains `validator_config` or the
  rubric. Non-owner project reads omit `creativity_scoring` (route tests plus the AC-8
  grep extended to rubric text).

**Creativity scoring**

- [x] AC-14: For a type with scoring, a submission is recorded with
  `judge_status=pending`, and its `is_valid` and class-flow events are unchanged. The job
  then writes all four scores (fluency computed, the rest 0-4) plus `provider`, `model`
  and `prompt_version` (worker test with a fake router).
- [x] AC-15: A judge agent not bound to the room yields `skipped`. Router exhaustion,
  non-JSON output or out-of-range scores yield `error` with no scores. A score still
  pending after 900 s is swept to `error`.
- [x] AC-16: A payload containing an instruction to award maximum scores reaches the model
  only inside the delimited JSON block. An output carrying anything but the expected keys
  is rejected (unit tests on prompt build and parse).
- [x] AC-17: Scores do not appear in `ActivitySubmissionOut`, the agent digest, the
  activity context, the room echo or any room event. They do appear in the dashboard
  participants table for an owner and in the export columns.
- [x] AC-18: A type can enable scoring only with an agent of its own project. A platform
  type cannot carry it, and editing it bumps the version (service tests).

**Overall**

- [x] AC-19: Backend lint, typecheck and tests (db tier included) pass in CI for each
  milestone, as do frontend lint, typecheck, tests and build.
- [ ] AC-20: On staging, a real export of a classroom-simulator room with a scoring-enabled
  type opens in Excel with readable Chinese, joins guest transcripts to submissions by
  code, and shows real judge scores (manual check recorded in the Deviation Log).

## 12. Test Plan

- **Unit** (`backend/tests/unit/`):
  - builder: AC-7 to AC-9;
  - export service state: AC-1, AC-3, AC-6;
  - worker with fakes: AC-4 to AC-6, AC-14 to AC-15;
  - `creativity_judge` prompt and parse: AC-16;
  - type service validation: AC-18.
- **DB tier** (`pytest.mark.db`): the revision writes and the `FOR SHARE` ordering
  (AC-10, AC-11), the export listing's `subject_kind` and guest join (AC-7), the
  migration up and down (AC-12), and the dashboard means query.
- **Route tests:** `latest`, 409, expired, the demoted GET and the confidentiality of
  `creativity_scoring` (AC-2, AC-3, AC-5, AC-13).
- **Frontend:**
  - component tests for re-attach, the notification action and the hidden creativity
    columns (AC-2, AC-17);
  - one Playwright spec for the export round trip.
- **Manual on staging:** AC-20, using the `bubble-check` or a CRAT simulator scenario
  with guests.

## 13. SRS Delta

Amend [R33.10] by appending:

> The bundle's manifest declares a `schema_version`. Rooms and the workspace are
> identified by truncated codes (`r:` and `w:`), and every participant row and message
> carries a `subject_kind` of `user`, `guest` or `group`. A guest's code is derived from
> their guest session and is the same in every file. Submissions are written as one CSV
> per activity type, UTF-8 with a byte-order mark, with each declared payload field
> flattened into its own column alongside the raw payload. A ready export stays
> downloadable until its record expires, and the record expires before its file can be
> purged. The owner can re-open the latest export of a workspace from the dashboard or
> from its ready notification. Export authority is re-checked when the export is built
> and when it is downloaded.

New entries, appended to section 30:

> **[R30.47]** Every behavioral definition of an `ActivityType` (`payload_schema`,
> `validator_kind`, `validator_config`, `group_config`, `creativity_scoring`) is kept as
> an immutable revision keyed by type and `version`, written in the transaction that
> creates the version. Each submission records the `version` it was validated under. A
> revision is confidential on the same terms as `validator_config` ([R30.25]); the
> research export carries only the version number.

> **[R30.48]** An `ActivityType` may enable creativity scoring by naming a judge agent of
> its own project and a rubric for flexibility, originality and convergence. Each
> submission of such a type is then scored asynchronously, without affecting its
> validation or `is_valid`:
>
> - fluency is the count of filled declared fields;
> - the other three are integer judgements from 0 to 4 with a short rationale, produced
>   with the judge agent's key group and model in the submission's room;
> - each score records the provider, model and prompt version.
>
> The participant's answer is passed to the model as delimited data, and output that
> does not match the expected shape is rejected. Scores are visible to Project Owners on
> the dashboard and in the research export only. They never appear on a participant
> surface, in agent context or in a room event, and they drive no automation. The rubric
> is confidential as `validator_config` is ([R30.25]).

## 14. Open Questions

- Should the dashboard show a class-level distribution chart of each dimension, and not
  just the per-participant means? This does not block approval: the means ship, and a
  chart is FU-2.

## 15. Deviation Log

Appended by /build.

- **D-1 (milestone 1).** A ready export whose object is gone answers 410 with the problem
  type `conversation/export-expired`, not 404. The user chose this during plan review: a
  410 tells the dialog the job existed and lapsed, while a 404 stays reserved for a job id
  the server never had or has forgotten. The frontend treats both as expired
  (`shared/utils/download.ts`).
- **D-2 (milestone 1).** The record's expiry counts from `upload_started_at`, captured
  just before the upload begins, rather than from the end of the upload. The bucket
  lifecycle counts from the object's creation, which is later, so the record always
  expires first; the spec's `uploaded_at` would leave a race equal to the upload time.
- **D-3 (milestone 1).** The per-owner, per-workspace index key
  (`research_export:latest:{owner}:{workspace}`) is both the latest pointer and the
  active-export slot, claimed with the same compare-and-swap takeover as the chat export.
  The takeover was extracted from `export_service.py` into
  `contexts/conversation/application/export_slot.py` so both exports share it instead of
  duplicating the Lua script. A holder older than twice the task timeout counts as
  abandoned and is marked failed on takeover.
- **D-4 (milestone 1).** The 409 is a new error, `ResearchExportInProgress`
  (`conversation/research-export-in-progress`), rather than the chat export's
  `ExportInProgress`, so each dialog matches only its own problem type.
- **D-5 (milestone 1).** The status GET keeps the stored-owner comparison and adds the
  computed-role re-check, instead of replacing one with the other. A job therefore stays
  readable only by its requester, as before; the re-check refuses that requester once
  they are no longer a Project Owner or admin.
- **D-6 (milestone 1).** Closing the dialog no longer waits for the job: it keeps
  polling in the background, so a completion still raises the toast, and reopening shows
  the same job. Starting a new export stays allowed once the previous one is ready, since
  a ready job does not hold the slot.
- **D-7 (milestone 1, from the quality gate).** Staleness counts from `running_since`,
  stamped by `mark_running`, and from `created_at` only while the job is still queued. A
  job that waited in the queue may then run its full timeout without being taken over.
  `mark_running` moves only a queued job, `mark_ready` only a running one, and
  `mark_failed` never overwrites a finished outcome, so a run whose job was taken over as
  abandoned cannot turn it back to ready or notify. `export_slot.claim` raises
  `SlotContended` instead of naming the caller's own deleted job when the slot changes
  hands on every attempt.
- **D-8 (milestone 1, from the quality and security gates).** The dialog's job logic
  lives in `slices/dashboard/composables/useResearchExport.ts`, following
  `useChatroomExport`. The dashboard accepts `?researchExport=` only as a UUID, passes it
  to the button as a fresh request object, and then removes it from the URL, so a reload
  does not reopen the job and following the same link again does. The job id is
  URL-encoded in the status request. A job the dialog found by itself that turns out
  failed or gone goes back to the form without an error toast.
- **D-9 (milestone 2).** Until milestone 3 adds revisions, a type's payload columns are
  the properties its current `payload_schema` declares, in declared order: `x-order`
  first, since jsonb does not keep key order. That ordering moved from
  `observation_aggregates.declared_fields` into `domain/property_order.py`, which both
  now use. The builder takes the union across rows in first-seen order, so milestone 3
  only has to hand it each row's revision. A property named like a fixed column stays in
  `payload_json` only. Until milestone 3 and 4, the CSV has no `type_version` or judge
  columns (section 10).
- **D-12 (milestone 2, from the gate).** Submissions are grouped into files by type id,
  not key, because a platform type and a project type, or a deleted type and its
  replacement, may share a key; the second file gets a `-2` suffix and `submission_files`
  is a list of `{type_key, file}`. Rooms whose eight-character codes collide within one
  export get `~1`, `~2` suffixes, so their transcripts are not merged under one key. The
  manifest carries `notes` stating that free text is not scrubbed and how formula cells
  are escaped.
- **D-10 (milestone 2).** A text cell starting with `=`, `+`, `-`, `@`, a tab or a carriage
  return is written with a leading apostrophe. The BOM is there so that the files open in
  Excel, and participant text reaches these cells, so an unescaped `=HYPERLINK(...)`
  would run as a formula on the researcher's machine (OWASP CSV injection). The JSON
  columns start with `{` and are unaffected.
- **D-11 (milestone 2).** The workspace code is `w:<8>`, parallel to `r:<8>`. The manifest
  also lists `submission_files`, mapping each type key to its CSV, since a file name is a
  sanitised and possibly suffixed key. Transcript entries gain `subject_kind` (`user`,
  `guest`, `agent` or `system`) beside the existing `sender_type`.
- **D-13 (milestone 3, approved with the plan).** The single migration 0108 of section 6
  is split. `0108_activity_type_revisions.py` holds only the versioning (the revisions
  table, `type_version`, the seed and the backfill), and the judging columns, including
  `activity_type_revisions.creativity_scoring`, arrive in 0109 with milestone 4, so each
  milestone reverts on its own.
- **D-14 (milestone 3, approved with the plan).** The locked version read happens at the start
  of `_record`, before validation. When the locked version differs from the type the
  caller loaded, because an edit committed in between, the type is reloaded and the
  payload re-checked against it, so the validator, the stored version and the schema the
  payload was checked against are the same definition. Section 5 stored the locked
  version without saying what to do on a mismatch.
- **D-15 (milestone 3).** Seeded revision rows take the migration's `now()` as
  `created_at` and a NULL `created_by`, since when and by whom a version above 1 came into
  being is recorded nowhere. `type_version` carries no foreign key to the revisions
  table: a type created by pre-0108 code during a rolling deploy has no revision row until
  its next bump.
- **D-16 (milestone 3, security gate, user's choice).** The version is pinned with a
  transaction-level advisory lock keyed on the type, not `FOR SHARE` on the type row.
  `_record` takes it shared and then reads the version; a version bump takes it
  exclusively before its UPDATE. A row lock conflicts with every UPDATE of the row, so
  under section 5's design a stream of submissions to a busy platform type would also
  hold up metadata edits and a platform admin's governance edit ([R30.23]) until the
  statement timeout. Section 10's note that the lock only orders a race that should not
  occur held for behavioural edits alone. The ordering between a submission and a bump is
  unchanged.
- **D-17 (milestone 3).** `type_version` reaches the domain only through
  `ResearchExportSubmission`, and no `ActivityTypeRevision` domain record exists, since
  nothing outside the repository reads a revision: the export joins each submission to its
  revision's `payload_schema` in the listing query, which is the one reader section 5
  needs. Section 6 listed both.
- **D-18 (milestone 4, user's choice).** Each session is judged at most
  `MAX_JUDGED_ATTEMPTS_PER_SESSION` (5) times; a later attempt is recorded `skipped` with
  reason `judge_cap` and never reaches the model. Section 5 assumed the submit route's rate
  limit bounded the judge's cost, but no per-participant limit on submissions exists, so a
  participant resubmitting in a loop would otherwise bill the owner's key without bound.
  The count covers `pending`, `scored` and `error`, so a skipped attempt does not use a
  slot.
- **D-19 (milestone 4, user's choice).** The dashboard means are shown to the project
  owner and platform admins only, not to every project member whom the dashboard
  otherwise admits ([R33.02]), because a score is a judgement about one participant and
  Q-11 limits it to the teacher. For any other member the scores are not read at all.
  They ride on the existing watchlist response as an optional `creativity` object rather
  than a new endpoint.
- **D-20 (milestone 4).** "Over their latest attempt per activation" reads as the latest
  scored attempt per activation, meaned across the room's activations. A subject whose
  latest attempt was skipped or failed keeps the earlier round scores, and the response
  carries the status and reason of the latest judged attempt so the dashboard can say why
  a cell is empty.
- **D-21 (milestone 4).** Section 5 said the judge's reply is "clamped". An out-of-range,
  non-integer or extra-keyed reply is instead rejected as `error` / `judge_bad_output`,
  because a number outside 0 to 4 shows the model did not follow the rubric's scale, and
  clamping would store a confident-looking score for a reply that was not one (AC-15).
  Only an overlong rationale is trimmed. `<` and `>` inside the answer are additionally
  escaped, so a participant cannot spell the closing delimiter.
- **D-22 (milestone 4).** `activity_submissions` gains `judge_reason` beside the columns
  section 5 lists, holding the closed reason vocabulary (`judge_not_bound`, `judge_cap`,
  `judge_timeout`, `judge_unavailable`, `judge_bad_output`, `judge_misconfigured`); the
  model's own text never goes there, since it is participant-influenced. `JudgeStatus` and
  the prompt live in `domain/creativity_judge.py`, not `application/`, as pure functions,
  and `_is_filled` moved from the validator plugins into `domain/fill_rule.py` so the
  fluency count and `filled_count` share one definition.
- **D-23 (milestone 4).** The agent-in-project check runs at the route, as the MCP
  validator's does, because the activities context may not import the agents context; the
  job repeats it before the call. Consequently an edit that resubmits a scoring
  configuration whose judge agent was since deleted is refused until scoring is turned off
  or another judge chosen. The project types read returns `creativity_scoring` under the
  same owner-or-admin gate as `validator_config`; the platform admin's cross-project
  listing (`admin_activities.py`) does not carry it.
- **D-24 (milestone 4).** The export does not carry the judge's rationales. Section 5's
  column list names none, and a rationale is model text about a participant's answer that
  the participant can influence.
- **D-25 (milestone 4).** The judge job runs with a 660-second timeout, not the 120 seconds
  first implemented. One router call under the default rotation policy can take a 60-second
  quota wait plus four attempts at a 120-second HTTP timeout with backoff, so 120 seconds
  cancelled calls that the provider had already billed, and the cancellation rolled back the
  `activity_judge` usage rows written in the same session. The job now also catches the
  cancellation, writes `judge_timeout` and commits under `asyncio.shield`, the pattern the
  export jobs use, so a key group with a longer policy still leaves its usage on record. A
  lookup that fails before the provider call is recorded as `judge_unavailable` rather than
  left `pending` for the watchdog to mislabel as a timeout.
- **D-26 (milestone 4 security gate).** The judge reads only the fields the type's schema
  declares, and an encoded answer over 32,000 characters is recorded as `skipped` with
  `judge_answer_too_large` instead of being sent, since the participant controls its size
  and the owner's key pays for it; a truncated answer would be scored as if it were whole.
  Lookalike angle brackets and the Unicode line separators are escaped with `<` and `>`,
  and a reply over 16,000 characters is rejected before parsing. `PROMPT_VERSION` is 2.
## 16. Follow-ups

- **FU-1.** A PENDING (MCP or webhook) validation can complete after its activation
  ends. If the type is then edited, the result is written under a version the
  submission did not record. Rare, and the async validators are unused on staging.
  Record `type_version` at validation write-back too, or refuse the write-back on a
  mismatch.
- **FU-2.** A per-dimension distribution chart on the dashboard (section 14).
- **FU-3.** Human override of judge scores, double scoring for inter-rater reliability,
  and rubric calibration.
- **FU-4.** Cohort-relative originality (TTCT rarity), recomputed when an activation
  ends.
- **FU-5.** The behavioral-edit guard `list_active_for_type` is a plain read
  (`type_service.py:229`). The `FOR SHARE` in this task orders submissions against
  edits, but an activation can still start while an edit commits. The guard could take
  the same lock.
- **FU-6 (milestone 1 quality gate).** `app/api/v1/research_export.py` imports the
  conversation application layer directly (pre-existing) and now also decides "ready but
  purged" with a MinIO `stat` in the route. Move both behind `ConversationFacade`.
- **FU-7 (milestone 1 quality gate).** `research_export_access.still_entitled` repeats
  the rule of `assert_computed_project_owner` (`app/api/v1/deps.py`), and the two
  exports' `_blocks` functions are near-identical. Share one "computed project owner"
  predicate, and give `export_slot` a staleness helper. `export_slot.claim` types its
  client as `Any`; a small protocol would do.
- **FU-8 (milestone 1 security gate, pre-existing).** The worker stores `str(exc)` as the
  job's `error`, which the status GET returns, and can carry internal host or bucket
  names. Store a generic message and log the detail.
- **FU-9 (milestone 1 security gate, pre-existing).** The research export routes answer
  404 for a missing workspace or job and 403 for someone else's, before any membership
  check, so existence is observable. The ids are random UUIDs; a uniform 404 would close
  it. A download ticket minted before a demotion stays valid for its 15 minutes, which is
  accepted.
- **FU-10 (milestone 1 quality gate, pre-existing).** The export dialog's Escape handler
  sits on a backdrop that never takes focus, so Escape does not close it.
- **FU-11 (milestone 2 security gate, pre-existing).** Free text in the bundle (answers,
  messages, observations) can name participants, and agents may address them by name.
  The manifest now says so. Scrubbing UUIDs and emails from free text, or a documented
  decision not to, belongs in [R33.10].
- **FU-12 (milestone 2 security gate).** `sub_scores_json` and `payload_json` pass through
  whatever an MCP or webhook validator returned. An allow-list of exported `sub_scores`
  keys would bound what an external validator can place in the bundle.
- **FU-13 (milestone 3 security gate, hardening).** `activity_type_revisions` keeps every
  past `validator_config` for the life of the type. A webhook URL carrying a credential
  (for example `?token=`) that an owner rotates by editing the type stays at rest in the
  older revisions. State that `validator_config` must not hold credentials, or redact URL
  query strings in revision rows.
- **FU-14 (milestone 4 security gate).** The judged-attempt cap is per session, and a
  guest's session is keyed by a browser id the client chooses, so one holder of the guest
  link can mint up to 50 guests (`max_guests_per_chatroom`) and get five judgements as
  each. Add a per-room bound on judge enqueues (for example a multiple of the roster
  size) and rate-limit the submit endpoint. A group member also gets five judgements
  individually and five for the group, which is accepted.
- **FU-15 (milestone 4 security gate, hardening).** Judge jobs share the main arq queue
  (`max_jobs = 50`) with agent turns and the watchdogs; a throttled key group can hold
  slots for up to the 660-second timeout. A dedicated queue or a per-project semaphore
  would isolate them. The dashboard `creativity_for_rooms` read has no time window and
  runs on every invalidation; bound it or debounce. `CreativityMeansOut.last_reason` could
  be a `Literal` like `last_status`.
- **FU-16 (milestone 4 security gate, hardening).** Judge rationales are stored in
  `judge_scores` but never read back. Either stop storing them or bring them under
  retention and erasure.
- **FU-17 (milestone 4 quality gate).** `_call_judge` resolves the provider and default
  model the same way `turn_engine._resolve_provider_and_model` and `prompt_assistant` do;
  expose one `AgentsFacade` helper. `creativity_for_rooms` assembles rows through a
  `dict[str, Any]` scratch structure that two typed dicts would replace, and
  `test_dashboard_creativity.py` carries an unexplained `type: ignore[arg-type]`.