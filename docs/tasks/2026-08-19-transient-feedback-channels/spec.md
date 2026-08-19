---
type: bugfix
status: draft
created: 2026-08-19
requirements: [R24.25]
depends_on: []
---

# Transient feedback channels: restore the toast layer and the 422 field-error contract

Source: `docs/audits/2026-08-19-page-presentation-scroll-and-feedback/findings.md`
(F-1, F-2, F-19, F-20, F-21, F-32, F-35, F-36, F-37, F-38).

## 1. Summary

Every path by which SMAP tells a user that something succeeded or failed is currently
broken, inconsistent, or silent. Two defects are critical. **F-1**: vue-sonner 2.x ships its
CSS as a separate export and no longer injects it at runtime, and nothing in the project
imports it, so `[data-sonner-toaster]` never receives `position: fixed` and every toast in
the product renders unstyled in normal document flow, starting one full viewport below the
fold. **F-2**: the backend emits raw Pydantic `exc.errors()` as `field_errors` while
`REQUIREMENTS.md` R24.25 and the whole frontend expect `{path, message}`, so a 422 sets no
inline field error, suppresses the fallback toast at roughly ten call sites, and leaves the
user with no feedback whatsoever. Eight further defects in the same layer are fixed
alongside, because they are the same question asked in different places: which channel
carries which message, how long it lives, and whether it is legible.

This dossier is the reported user complaint's primary cause. The audit was opened because
"messages pop up at the bottom of the page", "there is blank space at the bottom", and "a
scrollbar appears for no reason"; F-1 alone produces all three.

## 2. Observed vs Expected

### F-1 (critical) - the toast stylesheet is never imported

- **Observed** - `frontend/package.json:50` pins `vue-sonner@^2.0.9`. Its
  `package.json` exports the CSS separately as `"./style.css": "./lib/index.css"`, and
  `lib/index.js` contains zero occurrences of the substring `css` and injects no style
  element, so 2.x does not self-install its stylesheet. Nothing imports it: the only
  `vue-sonner` references outside `node_modules` are `src/app/App.vue:4`,
  `src/app/errorHandler.ts:2`, `src/shared/composables/useToast.ts:1`,
  `src/shared/styles/main.css:396`, `vite.config.ts:55`, `package.json:50` and three test
  mocks. Everything that makes a toast a toast lives only in the unimported file:
  `lib/index.css:19-21` (`position: fixed`), `:43` (`z-index: 999999999`), `:51-64`
  (corner offsets), `:385-397` (mobile full-width). `<Toaster>` (`src/app/App.vue:49-52`) is
  therefore a plain flow sibling rendered after the layout, and `AppShell` is exactly
  `height: 100vh` (`src/app/layouts/AppShell.vue:141`), so the toast list begins at
  y = 100vh. The theming block at `src/shared/styles/main.css:399-440` recolours an element
  that has no positioning, and its comment at `:395-398` ("The double-attribute selectors
  outrank sonner's runtime-injected base styles") describes vue-sonner 1.x behaviour.
- **Expected** - `docs/UI/12-shared-patterns.md` §4.1 (Toast row) and §9: a corner-anchored,
  auto-dismissing overlay with per-type durations, above ordinary page content.

### F-2 (critical) - `field_errors` wire shape

- **Observed** - `backend/shared_kernel/errors/handlers.py:63` emits
  `extras={"field_errors": exc.errors()}`, i.e. Pydantic v2's `{ctx, input, loc, msg, type}`,
  and is the only producer of that key in the repository. The generated client records the
  same shape at `frontend/src/shared/api-client/models/ValidationError.ts:5-11`. The frontend
  types it `{path, message}` (`frontend/src/shared/errors/index.ts:47-53`) and reads
  `fe.path`/`fe.message` (`frontend/src/shared/composables/useServerErrors.ts:32-40`);
  `frontend/src/shared/transport/problem-json.ts:16,47-49` re-declares the same wrong type
  and normalises nothing. So `fieldErrors.length > 0` passes the guard at
  `useServerErrors.ts:33`, `mapped["undefined"] = undefined` is handed to vee-validate's
  `setErrors` and silently dropped, and the function returns `true`, which every call site
  reads as "the user has been told" and uses to skip its own `toast.error`.
- **Expected** - `REQUIREMENTS.md:1942` (R24.25) specifies `field_errors` as
  `{path, message}`; `docs/UI/12-shared-patterns.md` §4.2 maps it to form fields and §4.1
  assigns validation failures to the Field level.

### F-19, F-20, F-21, F-32, F-35, F-36, F-37, F-38

Stated in full in the audit; each is restated in §5 with its causal chain. Summarised:

| ID | Observed | Expected |
|---|---|---|
| F-19 | `useAdminActions.ts:59,65,116,121` toast an error, and `AdminAdminsView.vue:137-139,154-160` / `AdminOpsView.vue:125-127,142-144` catch the same rejection to set a second, differently worded `SAlert` that has no timer and is not dismissible | `docs/UI/12-shared-patterns.md:550` "One toast per action"; §4.1 assigns one level per error. `AdminIpBansView.vue:139-141` already models the correct contract |
| F-20 | `errorHandler.ts:21,39` hardcode English; `:13` pipes backend `detail` verbatim; all four sites use raw `toast` rather than `useToast()` | Project rule "all user-facing strings go through `$t()`"; `docs/UI/12-shared-patterns.md` §4.2 specifies a fixed UI string for `forbidden` |
| F-21 | `WorkflowListView.vue:175-180` awaits `mutateAsync` with no `try`, so a failed create reaches `ErrorBoundary.vue:17-30` and replaces the whole list | Internal consistency: every other `mutateAsync` call site is wrapped |
| F-32 | `SNetworkBanner.vue:41-49` is fixed to the viewport top at `--z-banner` (350), over the 56px top bar | `docs/UI/12-shared-patterns.md:323` "fixed at top of **content area**" |
| F-35 | `--z-toast: 500` (`main.css:86`) is declared and never consumed; the real value is sonner's `999999999` | `docs/UI/01-design-system.md` z-index scale |
| F-36 | `<Toaster>` gets no `containerAriaLabel`, so the live region announces "Notifications alt+T" in English (`vue-sonner/lib/index.js:920,944,980,1151`) | Project `$t()` rule |
| F-37 | Version conflicts are `toast.warning` in tenancy/conversation and `toast.error` in prompt-studio/skills | `docs/UI/12-shared-patterns.md:546` assigns warning |
| F-38 | `ProfileView.vue:153-159` and `AdminOpsView.vue:27-34,66-73` render transient success as a `focus-on-mount` `SAlert` with no timer | `docs/UI/12-shared-patterns.md:544` (success is a 4s toast) |

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | F-2: fix the backend to emit `{path, message}`, or adapt the frontend to Pydantic's shape and amend the SRS? | Fix the backend. `handlers.py` normalises `exc.errors()` into `{path, message}` before it reaches `extras`. | `REQUIREMENTS.md:1942` (R24.25) is authoritative and the entire frontend already implements it, so the backend is the single deviating component. Adapting the frontend instead would mean amending the SRS to match an accident of the FastAPI default handler, and would leak a framework's internal error shape into a public API contract. The change is confined to one function. |
| Q-2 | F-2: how is `path` derived from Pydantic's `loc` tuple? | Drop the leading source segment (`body`, `query`, `path`, `header`, `cookie`) when present, then join the remainder with `.`, rendering integer indices as `[n]`. `message` is `msg`. | vee-validate keys errors by the field name the form registered, which never includes the request-location prefix. Array indices must survive so nested list fields can be addressed. |
| Q-3 | F-2: does this break the OpenAPI contract and the generated client? | Yes, deliberately. `ValidationError` in the published schema changes, so `pnpm run gen:api` must be re-run and the regenerated types committed in the same change. | `frontend-gate-openapi-drift` fails otherwise. Prior dossiers have been caught by exactly this gate; see BOARD.md's note on `2026-08-16-platform-type-delete-optin-lifecycle` D-8, including the UTF-8 BOM hazard when regenerating on Windows. |
| Q-4 | F-1: import the stylesheet globally, or scope it? | Global, in `src/app/main.ts`, next to the existing `@shared/styles/main.css` import. | The Toaster is mounted once in `App.vue` and is always present. A lazy import would leave the first toast of a session unstyled. |
| Q-5 | F-1: keep the local theming overrides in `main.css:399-440`? | Keep them, and correct the stale comment at `:395-398`. Verify after the fix that they still win over the now-present base stylesheet. | The overrides implement the project's token-based tinting and are wanted. Their specificity claim was written against 1.x's runtime injection and must be re-checked against a real stylesheet, which is import-order dependent. |
| Q-6 | F-35: apply `--z-toast` to the toaster, given sonner hardcodes `999999999`? | Yes. Set the toaster's z-index to `var(--z-toast)` in the override block. | A nine-digit z-index means no project layer can ever sit above a toast, which is wrong for the impersonation banner and for modals. 500 keeps the toast above chrome and modals but below tooltips, as the scale intends. Note this interacts with F-5 in `2026-08-19-shared-overlay-and-shell-defects`, which lowers the impersonation banner from 9999 into the scale. |
| Q-7 | F-32: move the banner into the content area, or leave it viewport-fixed and offset it? | Offset it below the top bar (`top: var(--topbar-height)`) rather than restructuring where it is mounted. | The banner is rendered outside the layout at `App.vue:27` and must also work on the auth and public layouts, which have no content area. An offset satisfies the spec's intent (it stops covering chrome) without making the component layout-aware. |
| Q-8 | F-38: what replaces the persistent success alerts? | `toast.success` via `useToast()`, and delete the `saved` / `resetResult` success refs. Keep the danger arm of `AdminOpsView`'s result as an `SAlert`, since a failed maintenance operation is a state the operator should be able to re-read. | Matches `docs/UI/12-shared-patterns.md` §9's split between transient and persistent. |

## 4. Reproduction

**F-1** (any environment, any route):
1. `pnpm dev`, log in, open any list view.
2. Trigger any success or failure, e.g. rename an org, or delete a chatroom message while
   offline.
3. Observe: no toast appears in any corner. Scroll the *document* (not the content area) to
   the very bottom and the toast is there, unstyled, below the app shell, and the document
   scrollbar disappears when it expires.

**F-2** (needs a request-validation 422, not a domain 422):
1. Open `/agents/:id` and the Tools tab; open the "Add MCP server" dialog.
2. Submit a payload that fails FastAPI request validation.
3. Observe: no inline field error, no toast, dialog stays open. Repeat clicks change nothing.

**F-19**: `/admin/admins`, promote a non-existent user id. Observe both a toast and a
persistent banner with different wording; the banner outlives the toast indefinitely.

**F-21**: `/workspaces/:wid/workflows` as a member without create rights, submit the new
workflow form. Observe the list replaced by the error-boundary fallback.

## 5. Root Cause Analysis

**F-1 root cause**: the vue-sonner 1.x to 2.x upgrade moved CSS delivery from runtime
injection to a separate export, and the upgrade did not add the import. The chain is:
`package.json:50` (2.x) to `lib/index.js` (no CSS side effect) to nothing importing
`vue-sonner/style.css` to `[data-sonner-toaster]` having no `position` to the toast list
laying out in flow after a `height: 100vh` shell (`AppShell.vue:141`) to a toast at
y = 100vh. The earliest link whose correction prevents the symptom is the missing import.
Aggravating, not causal: `main.css:395-398`'s stale comment, which asserts the opposite and
would have prevented anyone reading that block from noticing.

**F-2 root cause**: `handlers.py:63` passes `exc.errors()` through unmodified. Every
downstream link is correct against R24.25; the earliest link whose correction prevents the
symptom is that line. Aggravating: `useServerErrors.ts:32-40` returns `true` after setting
nothing, which converts a mapping failure into silence rather than a fallback. Even with the
backend fixed, that function should not claim an error it did not surface, so both are
corrected (see §7).

**F-19**: two independent error channels for one mutation, neither aware of the other; the
composable's `onError` and the view's `catch`.

**F-20**: `errorHandler.ts` was written before the shared locale bundle gained
`shared.errors.rateLimited` (which `useServerErrors.ts:28` uses), and was never revisited.

**F-21**: one missing `try`/`catch`, made severe by F-6 (`ErrorBoundary` wrapping the whole
layout), which is owned by `2026-08-19-shared-overlay-and-shell-defects`.

**F-32**: the component was written as a viewport-level overlay while the spec describes a
content-area banner; nothing reconciled them.

**F-35**: the token was added to the scale but the toaster was never wired to it, because
sonner's own stylesheet was assumed to be doing the positioning (and, per F-1, was not
present at all).

**F-36, F-37, F-38**: omissions and drift, no shared cause.

## 6. Blast Radius and Sibling Suspects

- **F-1 blast radius**: every toast in the product, on every route, for every user, in every
  environment including production. No data impact.
- **F-2 blast radius**: every form that can receive a request-validation 422. Roughly ten
  call sites listed in the audit. Also `frontend/src/shared/errors/index.ts:91-93`, which
  renders the literal `undefined: undefined` into page banners via the keys query composables
  (`useMyKeys.ts:16`, `useKeyGroups.ts:22`, `useSearchKeys.ts:21`, `useProjectKeys.ts:18`,
  `useKeyProjects.ts:25`); reachable only on a 422 from a GET, so rarer but real.

**Sibling suspects**

- Other third-party stylesheets that might be missing the same way: **cleared**. Vue Flow's
  is imported correctly at `slices/workflow/views/WorkflowEditorView.vue:345,347` and
  `slices/agents/views/GraphragGraphView.vue:22,24`. No other CSS-shipping dependency was
  found.
- Other producers of `field_errors` in the backend: **cleared**, `handlers.py:63` is the only
  one (repository-wide grep returns that line, a comment in `problem.py:31`, and
  `REQUIREMENTS.md:1942`).
- Other `if (!applyServerErrors(err))` call sites beyond the ten listed: to be enumerated
  during the build; the audit's list came from a grep and should be re-run against the tree
  at build time.
- Other views pairing a composable-level toast with a view-level catch (the F-19 pattern):
  `AdminIpBansView.vue:139-141` is **cleared** (empty catch with a deferring comment). The
  rest of `slices/admin/views/` must be swept during the build.
- Other unguarded `mutateAsync` awaits (the F-21 pattern): verification found
  `WorkflowListView.vue:178` to be the only one, every other call being inside a `try`. Re-run
  the sweep at build time as a guard against drift.

## 7. Fix Design

1. **F-1**: add `import 'vue-sonner/style.css'` to `frontend/src/app/main.ts`, adjacent to the
   existing `@shared/styles/main.css` import and before it, so the project's override block
   cascades last. Correct the stale comment at `main.css:395-398` to say that the base
   stylesheet is imported in `main.ts` and that these rules override it by specificity and
   order. Re-verify each override still applies.
2. **F-35**: in the same override block, set
   `[data-sonner-toaster] { z-index: var(--z-toast); }`.
3. **F-2**: in `backend/shared_kernel/errors/handlers.py`, map `exc.errors()` to
   `[{"path": <derived per Q-2>, "message": e["msg"]}]` before putting it in `extras`. Keep
   the raw list out of the response entirely: `input` can contain user-submitted values and
   has no business in an error body. Regenerate the OpenAPI schema and the frontend client
   (`make openapi-types` / `pnpm run gen:api`), watching for the UTF-8 BOM hazard. Separately,
   harden `frontend/src/shared/composables/useServerErrors.ts` so it returns `true` only if it
   actually set at least one error that a registered field owns, so a future shape drift
   degrades to a visible toast instead of silence.
4. **F-19**: delete the view-level `catch` bodies in `AdminAdminsView.vue` and
   `AdminOpsView.vue` that duplicate the composable's `onError`, following
   `AdminIpBansView.vue:139-141`. Remove the now-unused error refs and their `SAlert`s.
5. **F-20**: route all three `errorHandler.ts` messages through `t(...)` and `useToast()`,
   reusing `shared.errors.rateLimited` and adding a key for the generic fallback. Replace the
   raw `err.detail` passthrough with the spec's fixed strings per problem type.
6. **F-21**: wrap `WorkflowListView.onCreate`'s `mutateAsync` in `try`/`catch`, with the catch
   empty and commented to defer to the mutation's `onError`.
7. **F-32**: change `SNetworkBanner.vue`'s `top: 0` to `top: var(--topbar-height)` and drop the
   `margin-top: 12px` compensation, keeping it centred.
8. **F-36**: pass a translated `containerAriaLabel` and `closeButtonAriaLabel` to `<Toaster>`.
9. **F-37**: change the prompt-studio and skills conflict paths to `toast.warning`.
10. **F-38**: replace the success `SAlert`s with `toast.success` per Q-8.

No data repair is required; nothing was persisted incorrectly.

## 8. Regression Test Plan

Written first, failing against current code.

- **T-1 (F-1)** `frontend/e2e/`: a spec that triggers a toast and asserts its container's
  computed `position` is `fixed` and its bounding box lies within the viewport. This is the
  assertion the existing suite lacks: `frontend/e2e/16-knowmap.spec.ts:121` and its peers use
  `toBeVisible()`, which passes for any non-empty box at any document position. Fails today
  because the container is `static` at y = 100vh.
- **T-2 (F-2, backend)** `backend/tests/unit/`: post a body that fails request validation and
  assert the response's `field_errors` is exactly `[{"path": "...", "message": "..."}]`, with
  a nested and an indexed case, and assert `input` does not appear anywhere in the body.
  Fails today because the keys are `loc`/`msg`/`type`/`input`.
- **T-3 (F-2, frontend)** rewrite
  `frontend/src/shared/composables/__tests__/useServerErrors.test.ts`: replace the
  hand-written fixture at `:42` with a payload captured from the real backend response, and
  add a case asserting that an unmappable payload returns `false` so the caller toasts.
  Fails today on the second case.
- **T-4 (F-19)** component test: a failing admin promote raises exactly one user-visible
  message.
- **T-5 (F-21)** component test: a rejected create mutation leaves the list rendered.
- **T-6 (F-37)** unit test on both conflict paths asserting `toast.warning`.
- **T-7 (F-38)** component test: a successful profile save leaves no standing alert in the DOM.

`frontend/e2e/11-mcp.spec.ts:57`'s comment recording the suppressed toast as accepted
behaviour must be removed, and its assertion extended to cover the 422 path.

## 9. Risks and Rollback

- **Importing the stylesheet changes the visual result of every toast**, from an unstyled
  in-flow block to a positioned overlay. That is the point, but it means the override block
  is being exercised for the first time; expect to adjust specificity. It also makes F-5
  (impersonation banner at `z-index: 9999`) and F-32 newly visible as overlaps, which is why
  Q-6 and Q-7 are in this dossier.
- **Changing `field_errors` is a public API contract change.** Any external consumer of the
  problem+json body that parses `loc`/`msg` breaks. Given the BYO-key self-hosted model and
  that R24.25 already specifies the target shape, this is the intended contract, not a
  regression. The OpenAPI drift gate will catch an incomplete regeneration.
- Rollback for each item is an independent revert; the ten findings are separable commits.

## 10. Acceptance Criteria

- [ ] AC-1: T-1 fails before the fix and passes after; a toast renders `position: fixed`
      inside the viewport on the configured corner.
- [ ] AC-2: `vue-sonner/style.css` is imported once, in `main.ts`, and the token-tinting
      overrides in `main.css:399-440` still apply to all four types in both themes.
- [ ] AC-3: the toaster's z-index resolves to `var(--z-toast)`, not `999999999`.
- [ ] AC-4: T-2 fails before the fix and passes after; `field_errors` matches R24.25 exactly,
      including a nested path and an array index, and carries no `input`.
- [ ] AC-5: the regenerated OpenAPI schema and frontend client are committed and
      `pnpm run check:openapi-drift` passes.
- [ ] AC-6: T-3 passes; `applyServerErrors` returns `false` when it maps nothing, and the
      caller's fallback toast fires.
- [ ] AC-7: a request-validation 422 on the Add MCP server dialog produces a visible message
      (inline where the path matches a field, otherwise a toast).
- [ ] AC-8: T-4 passes; no admin action produces both a toast and a standing banner.
- [ ] AC-9: T-5 passes; a failed workflow create leaves the list rendered.
- [ ] AC-10: no user-visible string in `errorHandler.ts` is an English literal or a raw
      backend `detail`; all resolve in both `en` and `zh-TW`.
- [ ] AC-11: `SNetworkBanner` does not overlap the top bar at any viewport width.
- [ ] AC-12: the toast live region's accessible name resolves through `$t()`.
- [ ] AC-13: T-6 and T-7 pass.
- [ ] AC-14: `frontend/e2e/11-mcp.spec.ts`'s accepted-behaviour comment is gone and the 422
      path is asserted.

## 11. SRS Delta

None. R24.25 already specifies the correct `field_errors` shape; this dossier brings the
backend into conformance with it rather than changing it.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1**: the e2e suite asserts toast text with `toBeVisible()`, which cannot distinguish a
  positioned overlay from an in-flow block anywhere in the document. T-1 fixes one spec; a
  shared helper asserting toast geometry should replace the pattern across the suite. Route to
  `check-quality`.
- **FU-2**: `frontend/src/shared/transport/problem-json.ts:16` duplicates the `field_errors`
  type declaration that `shared/errors/index.ts:47-53` also carries. One of them should own it.
- **FU-3**: the audit found no dependency-upgrade check that would catch a package moving from
  runtime style injection to a separate CSS export. Worth a note in
  `docs/dependency-holds.md` or the release checklist.
