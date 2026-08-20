---
type: bugfix
status: implemented
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
`REQUIREMENTS.md` R24.25 and the active frontend transport expect `{path, message}`, so a 422 sets no
inline field error, suppresses the fallback toast at roughly ten call sites, and leaves the
user with no feedback whatsoever. Eight further defects in the same layer are fixed
alongside, because they are the same question asked in different places: which channel
carries which message, how long it lives, and whether it is legible.

This dossier is the reported user complaint's primary cause. The audit was opened because
"messages pop up at the bottom of the page", "there is blank space at the bottom", and "a
scrollbar appears for no reason"; F-1 alone produces all three.

## 2. Observed vs Expected

### F-1 (critical) - the toast stylesheet is never imported

- **Observed** - `frontend/package.json:50` declares `vue-sonner@^2.0.9`, resolved to 2.0.9
  by `pnpm-lock.yaml:102-104`. Its
  `package.json` exports the CSS separately as `"./style.css": "./lib/index.css"`, and
  `lib/index.js` contains zero occurrences of the substring `css` and injects no style
  element, so 2.x does not self-install its stylesheet. Nothing imports it: the only
  `vue-sonner` references outside `node_modules` are `src/app/App.vue:4`,
  `src/app/errorHandler.ts:2`, `src/shared/composables/useToast.ts:1`,
  `src/shared/styles/main.css:396`, `vite.config.ts:55`, `package.json:50` and four test
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
  and is the only runtime producer of that key in the repository. Independently, FastAPI's
  generated OpenAPI still advertises its default `HTTPValidationError.detail[]` /
  `ValidationError` shape and `application/json` (`backend/openapi.json:5439-5450,9861-9899`),
  although the handler emits a top-level RFC 7807 extension member as
  `application/problem+json`. The active frontend transport types it `{path, message}`
  (`frontend/src/shared/errors/index.ts:47-53`) and reads
  `fe.path`/`fe.message` (`frontend/src/shared/composables/useServerErrors.ts:32-40`);
  `frontend/src/shared/transport/problem-json.ts:16,47-49` re-declares the same wrong type
  and normalises nothing. So `fieldErrors.length > 0` passes the guard at
  `useServerErrors.ts:33`, `mapped["undefined"] = undefined` is handed to vee-validate's
  `setErrors` and silently dropped, and the function returns `true`, which every call site
  reads as "the user has been told" and uses to skip its own `toast.error`.
- **Expected** - `REQUIREMENTS.md:1942` (R24.25) specifies `field_errors` as
  `{path, message}`; `docs/UI/12-shared-patterns.md` §4.2 maps it to form fields and §4.1
  assigns validation failures to the Field level. Both documents currently say
  `detail.field_errors`, although `Problem.detail` is a string and `Problem.dump()` flattens
  extension members at the top level (`backend/shared_kernel/errors/problem.py:42-56`); the
  approval delta corrects that wording without changing the intended item shape.

### F-19, F-20, F-21, F-32, F-35, F-36, F-37, F-38

Stated in full in the audit; each is restated in §5 with its causal chain. Summarised:

| ID | Observed | Expected |
|---|---|---|
| F-19 | `useAdminActions.ts:59,65,116,121` toast an error, and `AdminAdminsView.vue:137-139,154-160` / `AdminOpsView.vue:125-127,142-144` catch the same rejection to set a second, differently worded `SAlert` that has no timer and is not dismissible | `docs/UI/12-shared-patterns.md:550` "One toast per action"; §4.1 assigns one level per error. `AdminIpBansView.vue:139-141` already models the correct contract |
| F-20 | `errorHandler.ts:21,39` hardcode English; `:13` pipes backend `detail` verbatim; all three toast-emitting sites use raw `toast` rather than `useToast()` | Project rule "all user-facing strings go through `$t()`"; `docs/UI/12-shared-patterns.md` §4.2 specifies a fixed UI string for `forbidden` |
| F-21 | `WorkflowListView.vue:175-180` awaits `mutateAsync` with no `try`, so a failed create reaches `ErrorBoundary.vue:17-30` and replaces the whole list | Internal consistency: every other `mutateAsync` call site is wrapped |
| F-32 | `SNetworkBanner.vue:41-49` is fixed to the viewport top at `--z-banner` (350), over the 56px top bar | `docs/UI/12-shared-patterns.md:323` "fixed at top of **content area**" |
| F-35 | `--z-toast: 500` (`main.css:86`) is declared and never consumed; the real value is sonner's `999999999` | `docs/UI/01-design-system.md` z-index scale |
| F-36 | `<Toaster>` gets no `containerAriaLabel`, so the live region announces "Notifications alt+T" in English (`vue-sonner/lib/index.js:920,944,980,1151`) | Project `$t()` rule |
| F-37 | Version conflicts are `toast.warning` in tenancy/conversation and `toast.error` in prompt-studio/skills | `docs/UI/12-shared-patterns.md:546` assigns warning |
| F-38 | `ProfileView.vue:153-159` and `AdminOpsView.vue:27-34,66-73` render transient success as a `focus-on-mount` `SAlert` with no timer | `docs/UI/12-shared-patterns.md:544` (success is a 4s toast) |

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | F-2: fix the backend to emit `{path, message}`, or adapt the frontend to Pydantic's shape and amend the SRS? | Fix the backend. `handlers.py` normalises `exc.errors()` into `{path, message}` before it reaches `extras`; remove the unused raw-Pydantic `Problem` declaration at `frontend/src/shared/types/index.ts:3-10`. | R24.25's intended item shape and the active transport already use `{path,message}`. Adapting the frontend would leak a framework-internal shape into the public API. The stale ambient type is unused but disproves the original claim that every frontend declaration was already consistent. |
| Q-2 | F-2: how is `path` derived from Pydantic's `loc` tuple? | Drop one leading source segment (`body`, `query`, `path`, `header`, `cookie`) when present. Join string segments with `.`, append integer indices directly as `[n]` (`items[0].name`, never `items.[0].name`), and use `msg` as `message`. Omit an entry whose remainder is empty so a root-level error falls through to form-level fallback feedback. | vee-validate keys never include the request-location prefix. Array indices must survive, while an empty path cannot identify a control and must not be claimed as inline feedback. |
| Q-3 | F-2: how does the published OpenAPI contract change? | Add typed validation-field/problem schemas and install a shared-kernel OpenAPI post-processor after route registration. It replaces only FastAPI-generated 422 responses with an `application/problem+json` reference to the top-level validation Problem schema, then removes the now-unreferenced default schemas. Regenerate `backend/openapi.json` and the frontend client. | Changing the exception handler alone has no effect on `app.openapi()` (`backend/scripts/export_openapi.py:40-45`). The existing drift gate only detects uncommitted regeneration; a focused OpenAPI contract test must prove the media type and schema. |
| Q-4 | F-1: import the stylesheet globally, or scope it? | Global, in `src/app/main.ts`, next to the existing `@shared/styles/main.css` import. | The Toaster is mounted once in `App.vue` and is always present. A lazy import would leave the first toast of a session unstyled. |
| Q-5 | F-1: keep the local theming overrides in `main.css:399-440`? | Keep them, and correct the stale comment at `:395-398`. Verify after the fix that they still win over the now-present base stylesheet. | The overrides implement the project's token-based tinting and are wanted. Their specificity claim was written against 1.x's runtime injection and must be re-checked against a real stylesheet, which is import-order dependent. |
| Q-6 | F-35: apply `--z-toast` to the toaster, given sonner hardcodes `999999999`? | Yes. Set the toaster's z-index to `var(--z-toast)` in the override block. | A nine-digit z-index means no project layer can ever sit above a toast, which is wrong for the impersonation banner and for modals. 500 keeps the toast above chrome and modals but below tooltips, as the scale intends. Note this interacts with F-5 in `2026-08-19-shared-overlay-and-shell-defects`, which lowers the impersonation banner from 9999 into the scale. |
| Q-7 | F-32: move the banner into the content area, or leave it viewport-fixed and offset it? | Keep the shared banner viewport-fixed, add a layout-neutral offset prop/class, and let `App.vue` enable the top-bar offset only when `layoutComponent` is `AppShell`. Auth and public layouts retain the ordinary 12px viewport inset. | A global `top: var(--topbar-height)` creates a false 56px gap where no top bar exists. App owns layout selection and may configure a shared primitive without making `shared/` import from `app/`. |
| Q-8 | F-38 and F-19: which channel owns admin operation results? | `toast.success` owns successful reset/restore/profile saves; the existing composable error toast owns failures. Delete the combined `OpResult` refs and standing alerts. Preserve the `last-admin` explanation by moving its typed problem mapping into `useAdminActions` before deleting the view-level error state. | These operations do not create a persistent page condition. One translated toast per action preserves useful specificity without duplicate or focus-stealing standing alerts. |
| Q-9 | F-36: how are both vue-sonner accessible labels supplied? | Pass `containerAriaLabel` directly and `closeButtonAriaLabel` through `toastOptions`, both translated. | Installed `ToasterProps` exposes `containerAriaLabel` but nests the close label in `ToastOptions`; a direct close-label attribute would not reach toast buttons. |
| Q-10 | F-19 sibling: include the duplicate impersonation error channel? | Yes. Remove `AdminImpersonateLauncher`'s duplicate error ref/alert and leave `useImpersonation` as the single error-toast owner. | The build-time admin sweep confirmed the same defect pattern at `useImpersonation.ts:38,47` and `AdminImpersonateLauncher.vue:47,82,89`; leaving it would violate AC-8. |
| Q-11 | F-37: how many conflict branches are in scope? | Four: prompt-studio template update, prompt-studio config save, skills save, and skills restore. | The earlier “both paths” wording undercounted two separate branches in `useSkillEditor.ts:94,137`. |
| Q-12 | Does this dossier depend on any other active task? | No. `depends_on: []` remains correct. | The active large-artifact task does not overlap. The overlay/shell, content-spacing, and mobile dossiers already form a downstream overlap chain beginning with this dossier; the chatroom task is independent. |

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

**F-2 root cause**: `handlers.py:63` passes `exc.errors()` through unmodified. The earliest
link whose correction prevents the runtime symptom is that line. Independently, FastAPI's
automatic 422 schema advertises a different body and media type, so handler-only regeneration
cannot repair the published contract. Aggravating: `useServerErrors.ts:32-40` returns `true` after setting
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
  `AdminIpBansView.vue:139-141` is **cleared** (empty catch with a deferring comment).
  `AdminImpersonateLauncher.vue:47,82,89` is **confirmed** against composable toasts at
  `useImpersonation.ts:38,47` and is included per Q-10.
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
3. **F-2**: add typed validation-field/problem models and a shared-kernel OpenAPI installer;
   call it from `app/main.py` after all routers are included so only FastAPI's generated 422
   responses are replaced with the runtime `application/problem+json` contract. In
   `backend/shared_kernel/errors/handlers.py`, map `exc.errors()` to
   `[{"path": <derived per Q-2>, "message": e["msg"]}]` before putting it in `extras`. Keep
   the raw list out of the response entirely: `input` can contain user-submitted values and
   has no business in an error body. Remove the unused stale raw-Pydantic frontend Problem
   type. Regenerate OpenAPI/client artifacts, watching for the UTF-8 BOM hazard. Separately,
   harden `useServerErrors.ts` to filter structurally invalid/empty `{path,message}` entries
   and return `true` only when it passes at least one valid entry to the form. Valid paths not
   owned by an inline MCP control remain visible through `AgentToolsView.formLevelErrors`.
4. **F-19**: move `last-admin` problem mapping into the demote mutation's single toast, then
   delete duplicate view-level error state/alerts in `AdminAdminsView.vue`, `AdminOpsView.vue`,
   and `AdminImpersonateLauncher.vue`, following `AdminIpBansView.vue:139-141`.
5. **F-20**: route all three `errorHandler.ts` messages through `t(...)` and `useToast()`,
   reusing `shared.errors.rateLimited` and adding a key for the generic fallback. Replace the
   raw `err.detail` passthrough with the spec's fixed strings per problem type.
6. **F-21**: wrap `WorkflowListView.onCreate`'s `mutateAsync` in `try`/`catch`, with the catch
   empty and commented to defer to the mutation's `onError`.
7. **F-32**: add a top-bar-offset prop/class to `SNetworkBanner`; `App.vue` enables it only
   for `AppShell`, while auth/public layouts retain the normal viewport inset.
8. **F-36**: add locale keys and pass a translated `containerAriaLabel` plus
   `toastOptions.closeButtonAriaLabel` to `<Toaster>` per Q-9.
9. **F-37**: change all four Q-11 conflict paths to `toast.warning`.
10. **F-38**: replace Profile and both AdminOps success alerts with `toast.success`; retain
    their existing error feedback through the single owner chosen in Q-8.

### Quality plan and reuse inventory

- Follow the existing `useToast()` wrapper and `useI18n()` / registered app/shared locale
  bundles; do not add raw vue-sonner calls or a second notification abstraction.
- Keep OpenAPI rewriting in `shared_kernel/errors`; it is cross-cutting transport policy and
  must not import a context. Reuse the Pydantic models as the schema source rather than
  duplicating a handwritten JSON schema.
- Existing debt deliberately not copied: `frontend/src/shared/transport/problem-json.ts` and
  `shared/errors/index.ts` duplicate the active field-error item type (FU-2).
- Preserve slice isolation: admin, identity, prompt-studio, skills, and workflow changes use
  their existing composables and locale bundles only.

No data repair is required; nothing was persisted incorrectly.

## 8. Regression Test Plan

Written first, failing against current code.

- **T-1 (F-1/F-35)** `frontend/e2e/`: a deterministic, non-skipping fixture action triggers a
  toast and asserts its container's computed `position` is `fixed`, computed z-index is
  `500`, and its bounding box lies within the viewport. This is the
  assertion the existing suite lacks: `frontend/e2e/16-knowmap.spec.ts:121` and its peers use
  `toBeVisible()`, which passes for any non-empty box at any document position. Fails today
  because the container is `static` at y = 100vh.
- **T-2 (F-2 runtime, backend)** `backend/tests/unit/`: exercise body, query, path, header and
  cookie locations plus nested/indexed `items[0].name` and a root-level error. Assert status
  422, `application/problem+json`, an exact top-level Problem body with only `{path,message}`
  items, fallback behavior for the empty path, and absence of submitted `input` anywhere.
  Fails today because the keys are `loc`/`msg`/`type`/`input`.
- **T-2b (F-2 OpenAPI, backend)** assert a representative generated 422 response exposes only
  `application/problem+json`, references the typed validation Problem, and its field items are
  exactly `{path,message}`. Fails today on media type, placement, and schema.
- **T-3 (F-2, frontend)** rewrite
  `frontend/src/shared/composables/__tests__/useServerErrors.test.ts`: replace the
  hand-written fixture at `:42` with a payload captured from the real backend response, and
  add a case asserting that a structurally invalid/empty payload returns `false` so the caller toasts.
  Fails today on the second case.
- **T-4 (F-19)** component/composable tests cover promote, demote including `last-admin`,
  reset, restore, and impersonation start/end; each failure raises exactly one translated
  message and no standing alert.
- **T-5 (F-21)** mount the workflow list inside `ErrorBoundary`, reject create, and assert the
  list remains rendered with exactly one error toast.
- **T-6 (F-37)** unit tests cover all four Q-11 conflict branches and assert `toast.warning`.
- **T-7 (F-38)** component tests positively assert success toasts for Profile save and both
  AdminOps operations, and assert no standing success alert remains.
- **T-8 (F-20)** focused error-handler tests assert translated permission, rate-limit, and
  generic fallback messages in both locales and prove backend `detail` is ignored.
- **T-9 (F-32/F-36)** component tests cover app/auth/public banner-host routing and the
  below-topbar modifier, while source assertions cover the `68px` app-shell and `12px`
  auth/public offsets. A real `vue-sonner` mount verifies translated toaster container and
  nested close-button labels in both locales.
- **T-10 (F-1 theming)** Playwright verifies browser-computed background, foreground, and
  border colors for success/error/warning/info against their tokens in light and dark themes.

`frontend/e2e/11-mcp.spec.ts:57`'s comment recording the suppressed toast as accepted
behaviour must be removed, and its assertion extended to cover the 422 path.

## 9. Risks and Rollback

- **Importing the stylesheet changes the visual result of every toast**, from an unstyled
  in-flow block to a positioned overlay. That is the point, but it means the override block
  is being exercised for the first time; expect to adjust specificity. It also makes F-5
  (impersonation banner at `z-index: 9999`) and F-32 newly visible as overlaps, which is why
  Q-6 and Q-7 are in this dossier.
- **Changing `field_errors` is a public API contract correction.** A consumer of the real
  runtime body that parses `loc`/`msg`, or a consumer generated from the currently false
  FastAPI `HTTPValidationError.detail[]` schema, can break. Focused runtime/OpenAPI contract
  tests guard the correction; the drift gate only guards committed regeneration.
- **OpenAPI rewriting is cross-cutting.** Limit replacement to FastAPI-generated validation
  422 responses and assert unrelated explicit 422 responses are not rewritten.
- Rollback for each item is an independent revert; the ten findings are separable commits.

### Security considerations

- Raw Pydantic errors include `input`, which can contain user-submitted secrets or other
  sensitive values. The response test must prove no `input` key/value survives serialization.
- Only location segments and Pydantic's validation message cross the boundary. No request
  body, context dict, traceback, internal path, or authorization data is returned.
- This task changes no authentication, authorization, tenancy filter, persistence, HTML
  rendering, dependency version, or agent/tool execution path. Security gate scope is the
  validation response and the frontend handling of untrusted problem bodies.

## 10. Acceptance Criteria

- [x] AC-1: T-1 fails before the fix and passes after; a toast renders `position: fixed`
      inside the viewport on the configured corner.
- [x] AC-2: `vue-sonner/style.css` is imported once, before `main.css`, and T-10 confirms the
      token-tinting overrides apply to all four types in both themes.
- [x] AC-3: T-1 resolves the toaster's computed z-index to `500` from `--z-toast`, not
      `999999999`.
- [x] AC-4: T-2 fails before the fix and passes after; top-level `field_errors` matches R24.25,
      covers every source prefix plus exact nested/indexed formatting, and carries no `input`.
- [x] AC-5: the regenerated OpenAPI schema and frontend client are committed and
      T-2b plus `pnpm run check:openapi-drift` pass.
- [x] AC-6: T-3 passes; `applyServerErrors` returns `false` when no structurally valid item maps, and the
      caller's fallback toast fires.
- [x] AC-7: a request-validation 422 on the Add MCP server dialog produces a visible message
      inline for its two inline paths, in the form-level alert for other valid paths, or by
      fallback toast for an invalid/empty path.
- [x] AC-8: T-4 passes; covered admin and impersonation actions produce one message each,
      including the specific `last-admin` explanation, never a toast plus standing banner.
- [x] AC-9: T-5 passes; a failed workflow create leaves the list rendered.
- [x] AC-10: no user-visible string in `errorHandler.ts` is an English literal or a raw
      backend `detail`; all resolve in both `en` and `zh-TW`.
- [x] AC-11: T-9 proves app/auth/public host routing and the correct layout-specific offset
      rules, so the banner clears the top bar without a phantom auth/public gap.
- [x] AC-12: T-9 proves toaster container and close-button accessible labels resolve through
      `$t()` in `en` and `zh-TW`.
- [x] AC-13: T-6 and T-7 pass.
- [x] AC-14: `frontend/e2e/11-mcp.spec.ts`'s accepted-behaviour comment is gone and the 422
      path is asserted.

## 11. SRS Delta

Amend R24.25's location wording while preserving its intended item shape. Review then widened
the item to carry its request part (D-12), so the shipped text is:

> - **[R24.25]** Backend RFC 7807 errors with a top-level extension member
>   `field_errors: [{location, path, message}]` are piped to vee-validate's `setErrors()` so
>   server-side validation appears as inline or form-level errors without ad-hoc plumbing.
>   `location` names the request part the failure came from (`body`, `query`, `path`, `header`,
>   `cookie`) and `path` is relative to it; only `body` entries are attached to form fields,
>   since a path or query failure can share a name with an input the user cannot correct.

Also correct the two matching `detail.field_errors` references in
`docs/UI/12-shared-patterns.md` to “top-level `field_errors` extension member” as contract
documentation, not a new requirement.

## 12. Deviation Log

- **D-1 — the backend contract fix required a published-schema policy, not only a handler
  rewrite.** Freshness review proved FastAPI's generated 422 schema was unrelated to the
  runtime Problem response. The build added a narrow global OpenAPI postprocessor, a typed
  validation Problem, a negative test preserving explicit custom 422 responses, and regenerated
  every affected client description.
- **D-2 — malformed JSON is deliberately not an inline field error.** FastAPI reports a parser
  character offset as an integer `loc` segment. Publishing that as `[n]` would falsely claim a
  form collection index, so `json_invalid` remains a form-level/fallback 422 with an empty
  `field_errors` list.
- **D-3 — the generated field item is closed and `instance` is required.** Independent quality
  review found that the first schema admitted extra item keys and nullable/optional `instance`
  despite the stricter runtime. The schema and contract tests now match the wire exactly.
- **D-4 — the installed vue-sonner API nests the close-button label.** The approved draft named
  a nonexistent top-level Toaster prop. The implementation passes `containerAriaLabel` directly
  and `closeButtonAriaLabel` through `toastOptions`, verified with a real Toaster mount.
- **D-5 — M-1 became a repeatable E2E gate.** The requested eight-case browser matrix is now
  T-10, which compares computed toast background, foreground, and border colors with their
  light/dark design tokens in Playwright.
- **D-6 — sibling coverage expanded during the build.** The admin sweep included impersonation
  start/end, reset/restore failures, and GraphRAG reset success. Workflow containment moved to
  an app-layer harness using the real `ErrorBoundary`; global error fallbacks now run in both
  locales.
- **D-7 — local shell limitations were delegated to clean CI.** Direct frontend lint,
  typecheck, build and focused tests passed, but the Windows-to-WSL typecheck self-test and
  OpenAPI shell wrapper could not locate their opposite-platform runtimes. Remote CI supplied
  the authoritative typecheck self-test and OpenAPI drift results.
- **D-8 — independent review found no remaining security or quality defect.** Raw request input
  is excluded from validation responses; no authentication, authorization, tenancy, persistence,
  secret-storage, or HTML-sanitization path changed. The quality review's runtime and coverage
  findings are recorded above and were fixed before publication.
- **D-9 (CI fix) — the deterministic toast trigger used the obsolete `/profile` path.** The
  first full-stack run reached the app's 404 because the live identity route is
  `/account/profile`; the E2E now drives that authoritative route. The browser-computed theme
  matrix in the same spec passed on that run.
- **D-10 (CI fix) — toast geometry is sampled after the entrance transition settles.** The
  second full-stack run proved fixed positioning and z-index, but the immediate bounding box
  was still translated about 25px upward by vue-sonner's mount animation. T-1 now polls the
  actual toast box until the complete rectangle is inside the viewport.

- **D-11 (review) — four i18n keys were left orphaned by the ownership sweep.** Moving admin
  promote/demote/reset/restore feedback onto the mutations retired
  `admin.users.promotionFailed`, `admin.users.demotionFailed`, `admin.ops.resetFailed` and
  `admin.ops.restoreFailed` without deleting them. No gate catches an unreferenced key, so they
  were removed from both locales by hand. `admin.actionErrors.restoreFailed` is a distinct key
  and is still live.
- **D-12 (review) — the field-error item now carries its request part.** Stripping the
  `body`/`query`/`path`/`header`/`cookie` prefix made a path parameter and a body field of the
  same name indistinguishable, so a client mapping errors onto a form could blame an input the
  user cannot correct. `ValidationFieldError` gained a required `location`, `path` stayed
  relative to it, and `useServerErrors` attaches `body` entries only — a validation error naming
  no body field now falls through to the caller's domain message. A `loc` with no recognised
  request part is dropped rather than guessed at; FastAPI is the only producer and always emits
  one. This closes FU-2 as a side effect: the item type is declared once in
  `shared/errors/index.ts` and imported by `problem-json.ts`.
- **D-13 (review) — the superseded 422 schemas are dropped only once nothing points at them.**
  The postprocessor popped `HTTPValidationError` and `ValidationError` unconditionally whenever
  any replacement happened. That is safe for the current spec (nothing else references them),
  but an explicit 422 that survives the replacement — the case D-1's negative test already
  covers — would have been left pointing at a deleted schema. The pop is now guarded by a
  reference walk over the finished document, with a test for the surviving-reference case.
- **D-14 (review, no change) — the 403 detail stays out of the toast.** Review initially
  proposed carrying the server's `detail` as the toast description, since the localized line
  cannot say which rule refused. `errorHandler.test.ts` shows that was deliberate: the message
  is treated as attacker-influenced and the test asserts it never reaches the toast. The
  localized-only behaviour is correct and was left alone.

- **D-15 (review) — the localized close-button label had no button to label.** vue-sonner 2.0.9
  declares `closeButton: { type: Boolean, default: false }` and renders the button only when it
  is true, so `closeButtonAriaLabel` alone was inert and a keyboard user could not dismiss a 6s
  error toast early. `ToasterAccessibility.test.ts` passed because it supplied its own
  `closeButton: true`, a configuration the app did not ship, so the spec could not fail on this.
  The Toaster configuration moved to `app/toasterProps.ts`, App.vue binds it, and the test now
  mounts the app's own props.
- **D-16 (review) — the toast layer sat under the impersonation banner.** `ImpersonationBanner`
  carried a literal `z-index: 9999`, which outranks `--z-toast: 500`, and sonner's 24px top
  offset puts the first top-right toast inside that bar's ~36px height. An impersonating admin
  saw every toast clipped. The banner now uses `--z-banner`, the same layer as the connection
  banner and the same rationale: above chrome, below modals and toasts.
- **D-17 (review) — the published 422 promised `field_errors` the domain path never sends.**
  The postprocessor rewrites every automatic 422 to `ValidationProblem`, but the same operations
  also answer 422 from a context error map (`auth/password-weak`, `skills/*`, `activities/*`,
  `workflow/*`, `prompt_studio/*`) via `context_handler`, whose body carries no `field_errors`.
  With the member required, the generated client typed it non-optional and a consumer would have
  dereferenced a missing value. `field_errors` is now optional on the schema, with a test that
  validates a real domain 422 body against it. Every other member (`type`, `title`, `status`,
  `detail`, `instance`) is genuinely emitted by both producers and stays required.

## 13. Follow-ups

- **FU-1**: the e2e suite asserts toast text with `toBeVisible()`, which cannot distinguish a
  positioned overlay from an in-flow block anywhere in the document. T-1 fixes one spec; a
  shared helper asserting toast geometry should replace the pattern across the suite. Route to
  `check-quality`.
- ~~**FU-2**~~: closed by D-12 — the `field_errors` item type is now declared once, in
  `shared/errors/index.ts`.
- **FU-3**: the audit found no dependency-upgrade check that would catch a package moving from
  runtime style injection to a separate CSS export. Worth a note in
  `docs/dependency-holds.md` or the release checklist.
