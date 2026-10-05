---
type: bugfix
status: approved
created: 2026-10-05
requirements: [R13.06]
depends_on: []
---

# A member's narrowed chat export includes every guest's messages

## 1. Summary

Permission-matrix row 19 gives a project member a narrowed chat export: their own messages plus
agent and system messages. The narrowing predicate was written before the `guest` sender type
existed and keeps every row whose sender type is not `user`, so since guests arrived it also keeps
every guest's messages, edit histories and attachment object paths. This dossier fixes finding F-11
of `docs/audits/2026-10-05-guest-anonymous-session/findings.md` by naming the sender types the
narrowed export keeps instead of the one it drops.

## 2. Observed vs Expected

- **Observed.** `MessageRepository.list_for_export` narrows with
  `sender_type != 'user' OR sender_id = own_user_id`
  (`backend/contexts/conversation/infrastructure/repositories/message_repo.py:346-352`); its docstring
  equates "non-user" with "agent, system" (`:335-337`). `SenderType.GUEST` was added on 2026-09-04
  (`backend/contexts/conversation/domain/models.py:12-16`). The export service serialises each
  returned message with its content, edit history and attachments
  (`backend/contexts/conversation/application/chat_export_service.py:110-150`), so guest rows bring all
  three. The unit test asserts the old predicate text (`backend/tests/unit/test_export_authz.py:143`)
  and its mock copies the predicate (`:209-214`), so it passes either way.
- **Expected.** `REQUIREMENTS.md:208` (row 19): "A `∘` export contains the caller's own messages plus
  all agent and system messages in the room; messages sent by *other users*, together with their edit
  histories and attachments, are excluded. The narrowing is applied in the query predicate, so
  excluded rows are never read." The domain enum documents the same scope
  (`models.py:19-32`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is a guest an "other user" under row 19? | Yes. The narrowed export keeps the caller's own messages and agent and system messages only. | Row 19 lists what is kept, not what is dropped; guests are human participants other than the caller, exactly the population the narrowing excludes. The member can still read guest messages in the room, so this restores the specified narrowing rather than a read boundary (audit F-11 blast radius). |
| Q-2 | Rename `ExportSenderScope.OWN_PLUS_NON_USER`? | No. | Its value `own_plus_non_user` is persisted in export job state and audit metadata (`backend/contexts/conversation/application/export_service.py:61,140`; `test_export_authz.py:453`); renaming would need a read-side compatibility shim for no behavioral gain. The docstring is corrected instead. |
| Q-3 | Does this depend on another dossier? | `depends_on: []`. | No active dossier edits `message_repo.py` or the export service. |

## 4. Reproduction

1. A room with guest links on; a guest posts a message with an attachment and edits it.
2. A project member (not owner or admin) requests a chat export of the room (JSON).
3. The archive contains the guest's message, its edit history and the attachment path.

## 5. Root Cause Analysis

The predicate encodes row 19 as a negative ("everything except other users"), which was equivalent
to the positive form only while the sender types were exactly `user`, `agent` and `system`. Adding
`guest` silently widened it; nothing tied the predicate to the enum, and the test asserted the SQL
text instead of the outcome.

## 6. Blast Radius and Sibling Suspects

- **Blast radius.** Every narrowed export (project members) of a room that ever had guest posts.
- **Sibling suspects** (sweep of `sender_type` comparisons in `backend/`, excluding tests):
  - `message_repo.py:187` (`distinct_user_sender_ids`, `== USER`): **cleared**, intentionally user-only;
    guest sessions are added separately by the roster route.
  - `message_service.py:279` (author check `is not USER`): **cleared**, guests edit through a separate
    branch (`backend/app/api/v1/messages.py:481` accepts `user` and `guest`).
  - `workflow/application/event_dispatch.py:57-58` (`sender_filter == sender_type`): **cleared**, an
    exact match with a `guest` option in the editor.
  - `research_export_builder.py:214-219` collapses guest rows to the literal `guest`: **confirmed** a
    related defect (guests indistinguishable), owned by the dashboard audit's
    `research-export-data-shape` dossier; FU-1.
  - `conversation/interfaces/facade.py:60` (`_HUMAN_SENDERS`): **cleared**, already includes `GUEST`.

## 7. Fix Design

- `message_repo.py` `list_for_export`: replace the narrowing with
  `sender_type IN ('agent', 'system') OR sender_id = own_user_id`, built from `SenderType` members, and
  correct the docstring. The caller is never a guest (row 19's guest cell is `✗`,
  `backend/contexts/conversation/application/access.py:466-472`), and session ids and user ids are
  independent random UUIDs, so `sender_id = own_user_id` cannot match a guest row.
- `models.py` `ExportSenderScope` docstring: "agent and system" stays, "non-user" wording removed
  from the member description (Q-2).
- **Existing debt.** `test_export_authz.py:143` asserts SQL text and `:209-214` re-implements the
  predicate in a mock; the new tests assert outcomes against a real database instead. The old text
  assertion is updated to the new predicate rather than deleted, since it guards the
  "applied in the WHERE clause" half of row 19.
- **Patterns to follow.** Enum-typed comparisons as in `message_repo.py:187`; db-tier tests in
  `backend/tests/integration/test_guest_identity_writes_db.py` (seeded room with guest rows).
- **Reuse inventory.** `SenderType`, the existing `own_user_id` parameter, the db-tier fixtures that
  create guest sessions and guest messages.

## 8. Regression Test Plan

Written first, failing against current code:

1. db tier (`pytest -m db`), new test in `backend/tests/integration/`: a room with a user message by
   the caller, one by another member, one guest message with an attachment, one agent and one system
   message; `list_for_export(own_user_id=caller)` returns exactly the caller's, the agent's and the
   system message; with `own_user_id=None` it returns all five.
2. Unit: `test_export_authz.py:143` updated to the positive predicate.
3. Service level: a narrowed `ChatExportService` run over the same seed serialises no guest content
   or attachment.

## 9. Risks and Rollback

- Members lose guest messages from narrowed exports, which is the specified behavior; owners and
  admins (`✓` cells) are unaffected.
- No migration, no API change. Rollback: revert the commit.

## 10. Acceptance Criteria

- [ ] AC-1: a narrowed export contains only the caller's own messages and agent and system messages;
  guest messages, their edit histories and their attachments are absent (verified against a real
  database).
- [ ] AC-2: a full (`✓`) export still contains guest messages.
- [ ] AC-3: backend lint, typecheck and tests, including the db tier, pass in CI.

## 11. SRS Delta

None. Row 19 already specifies the intended scope.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1.** `research_export_builder.py:214-219` labels every guest row with the literal `guest`, so
  guests are indistinguishable in research datasets. Route to `research-export-data-shape` from the
  2026-10-05 dashboard audit.
