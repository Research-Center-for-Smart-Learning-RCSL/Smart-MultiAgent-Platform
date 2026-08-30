---
type: bugfix
status: draft
created: 2026-08-30
requirements: [R24.12, R24.35, R24.38]
depends_on: [2026-08-30-chatroom-approval-and-overlay-discoverability]
---

# Frontend shared abstraction contracts

## 1. Summary

Close two 2026-08-20 frontend follow-ups that bypass shared runtime contracts. Two error
handlers test the generated client's `ApiError` class even though the configured transport
throws `shared/errors.ApiError`, leaving message-pagination recovery and prompt-session expiry
branches dead (`2026-08-20-orchestration-room-scoped-reads` FU-5). Three copy actions call
`navigator.clipboard` directly instead of the existing guarded `useClipboard`; one reports
"Loading..." on failure and another reports success when the API is absent
(`2026-08-20-onboarding-without-smtp` FU-12). The fix moves all five sites onto their shared
abstractions and adds ESLint contract guards so the mismatch cannot be reintroduced.

Freshness was verified against `origin/main` at `73125821` (2026-08-28). All five production
sites and both false-positive test fixtures remain present; §5 cites the current paths.

## 2. Observed vs Expected

- **Observed — typed errors.** `useChatroomMessages` and `usePromptAssistantSocket` import
  `ApiError` from the generated tree and use `instanceof` to select their 422 and 404 recovery
  paths (`frontend/src/slices/conversation/composables/useChatroomMessages.ts:11,187-203`,
  `frontend/src/slices/prompt-studio/composables/usePromptAssistantSocket.ts:3,93-105`). The
  Axios response interceptor parses problem+json into the different class exported from
  `@shared/errors` (`frontend/src/shared/transport/problem-json.ts:1-9,31-60`,
  `frontend/src/shared/transport/axios.ts:193-205,209-227`). The branches therefore do not run
  for real transport failures.
- **Expected — typed errors.** All problem+json responses become subclasses of the single
  `shared/errors.ApiError` hierarchy ([R24.12], [R24.35]); consumers that branch on status or
  type must test that hierarchy rather than an uninstrumented generated implementation.
- **Observed — clipboard behavior.** `useEntityLifecycle` dereferences
  `navigator.clipboard.writeText` without an availability guard and displays
  `tenancy.common.loading` if the promise rejects
  (`frontend/src/slices/tenancy/composables/useEntityLifecycle.ts:75-78`).
  `ChatroomSettingsView` uses optional chaining, then unconditionally displays the success toast,
  so an absent Clipboard API makes `await undefined` look successful
  (`frontend/src/slices/conversation/views/ChatroomSettingsView.vue:262-271`). `ChatroomView`
  handles both failure forms correctly but duplicates the policy
  (`frontend/src/slices/conversation/views/ChatroomView.vue:993-1004`).
- **Expected — clipboard behavior.** `useClipboard.copy` guards API absence and rejection and
  returns a boolean to let each caller select truthful feedback
  (`frontend/src/shared/composables/useClipboard.ts:27-59`). Transient user feedback goes through
  the toast service and must describe the actual outcome ([R24.38]).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which follow-ups form this change? | FU-5 of `2026-08-20-orchestration-room-scoped-reads` and FU-12 of `2026-08-20-onboarding-without-smtp`. | Both are confirmed cases where production code bypasses a shared frontend abstraction while tests or ad-hoc guards make the code appear safe. They share the ESLint configuration, frontend gate, and a small, reviewable test surface. |
| Q-2 | Does this depend on another dossier? | Yes: `2026-08-30-chatroom-approval-and-overlay-discoverability`, for file-overlap sequencing only. | Both changes edit `ChatroomView.vue`, its imports/setup, and its component tests. Either design can exist independently, but building this one second avoids competing edits in the largest conversation view. The onboarding-policy dossier touches different identity/admin files and remains independent. |
| Q-3 | Should consumers use structural status checks instead of `instanceof`? | No. Import `ApiError` from `@shared/errors` and retain the status checks. | [R24.35] deliberately defines one typed hierarchy. Structural checks would hide a future transport regression that stopped producing the promised class. |
| Q-4 | What feedback does each copy caller show? | Preserve existing successful feedback where it exists; show the caller's existing copy-failure text only when `copy()` returns false. `useEntityLifecycle` uses `common.copyFailed` and adds no success toast. | This corrects false or nonsensical feedback without adding notification noise. Both conversation views already own specific success/failure strings; the shared common bundle already has an actionable failure string (`frontend/src/shared/locales/en.json:6-10`). |
| Q-5 | How is the error import guarded? | Add a named-import restriction for `ApiError` from `@shared/api-client` outside the ignored generated tree, and include it in every `no-restricted-imports` option builder used by base, per-slice, store and session overrides. | Flat-config overrides replace rather than merge a rule. Updating only the base rule would leave every slice file unguarded (`frontend/eslint.config.js:184-187,248-294`). Other generated models and services remain legal imports. |
| Q-6 | How is direct clipboard access guarded? | Add a `no-restricted-syntax` selector for calls to `navigator.clipboard.writeText`; disable that selector only for `shared/composables/useClipboard.ts`. | This targets the unsafe operation rather than banning `navigator`, clipboard mocks, or feature detection. The composable remains the one implementation boundary. |
| Q-7 | Do compiler-gate FU-2/FU-8 belong here? | No. | A strict-template probe produces about 660 errors across 121 Vue files, and test/e2e typechecking also exposes a broad existing backlog. Those are staged toolchain programmes, not a prerequisite for correcting five runtime sites. |

## 4. Reproduction

### R1 — deleted message anchor never reaches recovery

1. Load a chatroom with at least one older-page anchor.
2. Delete that anchor server-side before the client requests the next page.
3. Let the endpoint return its problem+json 422 response.
4. Observe that the transport throws `shared/errors.ValidationError` (a shared `ApiError`
   subclass), while the generated-class `instanceof` check is false. The generic failure toast
   is shown and the poisoned anchor remains, so the next attempt repeats the same failure.

Deterministic test: reject `listMessages` with `new ValidationError(...)` from
`@shared/errors`. The current tests instead construct the generated class
(`frontend/src/slices/conversation/__tests__/useChatroomMessages.test.ts:13-18`), so they do not
reproduce production behavior.

### R2 — expired prompt session is not marked expired

1. Leave a prompt-assistant tab open beyond the server session TTL.
2. Reconnect and let `getSession` return problem+json 404.
3. Observe that the shared `ApiError` misses the generated-class branch and
   `sessionExpired` remains false.

Deterministic test: reject `getSession` with the shared `ApiError` class. The current fixture uses
the generated class (`frontend/src/slices/prompt-studio/__tests__/usePromptAssistantSocket.test.ts:13,53-54`).

### R3 — Clipboard API unavailable

1. Run the frontend in a context without `navigator.clipboard` (non-secure origin or jsdom).
2. Copy a project/org id: property access throws synchronously before the `.catch` handler.
3. Copy a chatroom guest link: the UI displays the success toast although nothing was copied.
4. Copy a message: the action correctly reports failure, demonstrating the three callers already
   disagree about the same platform condition.

## 5. Root Cause Analysis

The generated OpenAPI client exports its own `ApiError`
(`frontend/src/shared/api-client/index.ts:1-6`), but the app instruments the Axios singleton used
by that client and replaces problem+json failures with the application error hierarchy before a
slice sees them (`frontend/src/shared/transport/axios.ts:209-227`). The two consumers and their
tests imported the visually identical generated class. The duplicate class identity is the root
cause of the dead branches; the wrong fixtures are the aggravating factor that made tests green.

For copying, `useClipboard` already centralizes the three relevant failure modes — missing API,
rejected permission/user activation, and caller-visible result
(`frontend/src/shared/composables/useClipboard.ts:1-14,38-52`). The three callers predate or
bypassed that abstraction. The absence of a lint boundary let their policies diverge. Direct
platform calls outside the shared composable are the root cause; the wrong tenancy key and the
optional-chain false success are consequences.

## 6. Blast Radius and Sibling Suspects

- The two confirmed generated-error imports are exactly the production occurrences outside the
  generated tree; a repository search finds their two matching test fixtures as the only other
  wrong imports. Other prompt-studio and conversation tests already use `@shared/errors`
  (`frontend/src/slices/prompt-studio/__tests__/feedbackSeverity.test.ts:6`,
  `frontend/src/slices/conversation/__tests__/approvalCardReconcile.test.ts:22`).
- The three confirmed production clipboard calls are exactly the direct
  `navigator.clipboard.writeText` occurrences outside `useClipboard`; the composable itself is
  already unit-tested and is the intended shared boundary.
- Existing data is unaffected: neither defect writes durable state. The message defect retains a
  stale client cache entry; reconnect/refetch can recover it. Prompt sessions continue to expire
  on the server even when the client misses the state. Clipboard failures copy nothing.
- No backend, tenant authorization, secret, or rendering path changes. Security review is limited
  to confirming that the lint selectors neither weaken existing import gates nor expose copied
  values to a new destination.

## 7. Fix Design

1. Change the two production imports and their two test fixtures to `ApiError` or the appropriate
   subclass from `@shared/errors`. Preserve the existing 422 retry bound and 404 expiry semantics.
2. Instantiate `useClipboard` in `useEntityLifecycle`, `ChatroomSettingsView`, and — after the
   dependency lands — `ChatroomView`. Await `copy(text)` and choose feedback from its boolean.
   Preserve each view's existing conversation-specific strings; use `common.copyFailed` for the
   tenancy composable.
3. Refactor `eslint.config.js` so one helper produces the complete `no-restricted-imports` options:
   existing cross-slice patterns plus the generated `ApiError` named-import ban. Every override
   that currently replaces the rule calls that helper with its own slice exception.
4. Add the direct Clipboard API call selector to the main TS/Vue rules and a one-file override for
   `src/shared/composables/useClipboard.ts`.
5. Add a small behavioral ESLint test using the installed ESLint API: a slice probe importing the
   generated `ApiError` fails, a slice probe calling `navigator.clipboard.writeText` fails, and the
   shared-composable file path permits the central call. This proves the flat-config override
   order rather than merely asserting configuration text.

No data migration, API regeneration, or SRS change is required.

## 8. Regression Test Plan

Tests are written or corrected before production code:

- `frontend/src/slices/conversation/__tests__/useChatroomMessages.test.ts`: change the 422 fixture
  to the shared error hierarchy. Confirm it fails before the import fix, then assert the dead
  anchor is removed, queries are reconciled, one retry occurs, and a second poisoned anchor stops
  without recursion.
- `frontend/src/slices/prompt-studio/__tests__/usePromptAssistantSocket.test.ts`: use the shared
  class for 404 and assert `sessionExpired`, messages and streaming state follow the existing
  expired-session contract.
- Add focused caller tests for all three copy sites. Stub `useClipboard.copy` to resolve true and
  false; assert success is never shown for false, the existing success text remains for true, and
  tenancy failure uses `common.copyFailed` rather than `tenancy.common.loading`.
- Add the behavioral ESLint test from §7. Run it against a slice file path so the per-slice
  override is exercised.
- Run `pnpm lint`, the focused Vitest files, `pnpm test`, `pnpm typecheck`, and `pnpm build`.
  `strictTemplates` and test/e2e typechecking are not gates for this dossier (Q-7).

## 9. Risks and Rollback

- **Flat-config replacement risk:** an incomplete helper could erase current slice/store import
  exceptions or make all imports illegal. The behavioral probes cover both prohibited operations;
  the existing boundary-enforcement job and full lint cover the established rules.
- **Clipboard lifecycle risk:** one composable instance owns one reset timer. None of these callers
  consumes the `copied` flag, so sharing it within a view cannot make the displayed state ambiguous.
- **Conversation overlap:** applying this before the chatroom overlay dossier would create avoidable
  conflicts in `ChatroomView` setup and tests. `depends_on` serializes the edits; re-verification at
  build start must use the post-overlay line locations.
- **Rollback:** revert the implementation commit. There is no schema or persisted-state change.
  The old runtime defects return immediately and visibly in the regression tests.

## 10. Acceptance Criteria

- [ ] AC-1: the message-pagination regression uses the shared transport error class, fails before
  the fix, and passes after it.
- [ ] AC-2: a real-shape 422 removes the poisoned anchor, reconciles cached messages, retries at
  most once, and does not repeat indefinitely.
- [ ] AC-3: the prompt-session regression uses the shared transport error class and a 404 sets the
  expired state while clearing stale message/stream state.
- [ ] AC-4: no production or test file outside `src/shared/api-client/**` imports `ApiError` from
  `@shared/api-client`.
- [ ] AC-5: project/org id, guest-link and message copy actions all call `useClipboard.copy` and
  show success only when it resolves true.
- [ ] AC-6: an absent Clipboard API and a rejected write produce truthful failure feedback at all
  three callers; tenancy uses `common.copyFailed`.
- [ ] AC-7: no direct `navigator.clipboard.writeText` production call exists outside
  `src/shared/composables/useClipboard.ts`.
- [ ] AC-8: ESLint rejects a generated `ApiError` named import from a slice even after per-slice
  override resolution, while leaving other generated-client imports legal.
- [ ] AC-9: ESLint rejects direct clipboard writes outside the shared composable and permits the
  implementation inside it.
- [ ] AC-10: existing cross-slice/store/session import restrictions still pass their enforcement
  gate; no lint rule is weakened to add these guards.
- [ ] AC-11: focused tests, full frontend tests, lint, current production typecheck and build pass.

## 11. SRS Delta

None. This bugfix restores the typed-error transport and shared-composable architecture already
specified by [R24.12], [R24.35] and [R24.38].

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- FU-1: Enable `vueCompilerOptions.strictTemplates` through a staged remediation programme. The
  2026-08-30 probe found about 660 errors across 121 Vue files, so a one-commit gate flip is not a
  safe extension of this task.
- FU-2: Add separate typecheck projects for unit and Playwright code after their current backlogs
  are classified. They remain FU-8 of `2026-08-19-shared-overlay-and-shell-defects`.
