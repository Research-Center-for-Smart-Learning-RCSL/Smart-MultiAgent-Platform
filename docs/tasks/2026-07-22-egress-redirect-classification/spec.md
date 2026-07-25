---
type: bugfix
status: approved
created: 2026-07-22
requirements: []
depends_on: []
---

# A 3xx from a function tool is delivered to the model as a successful empty result

## 1. Summary

The egress proxy deliberately does not follow redirects, because the redirect target has not
passed the per-project allowlist or the IP screen. It relays the 3xx faithfully, `Location`
header included. But the caller discards the headers, and `EgressOutcome.ok` is
`200 <= status < 400`, so a redirect arrives at the model as a **successful tool result with
an empty body**. A `local_function` pointed at a URL whose upstream normalises a trailing
slash returns nothing, is flagged as success, and the model answers from nothing. The
configurator's Test button uses the same predicate and reports the tool healthy.

Because the proxy does not follow redirects, a 3xx response body is empty *by construction* —
this is a guaranteed data-loss condition, not a probabilistic one. A predicate that calls a
guaranteed-empty response "ok" is internally inconsistent with the proxy configuration it
sits behind.

Source: `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-10 (major, confirmed).

## 2. Observed vs Expected

- **Observed.**
  - `backend/services/egress_proxy/app.py:204` — the proxy client is built with
    `follow_redirects=False`. This is the only occurrence in the repo, with no env override or
    settings knob, so it is genuinely the deployed configuration.
  - `app.py:437-438,456-478` — the 3xx status and headers are relayed; `Location` survives,
    since the hop-by-hop strip set (`:383-396`) covers only `connection`, `keep-alive`,
    `proxy-*`, `te`, `trailer`, `transfer-encoding`, `upgrade`, `content-length` and
    `content-encoding`.
  - `backend/contexts/agents/infrastructure/egress_client.py:122` returns
    `(status_code, dict(resp.headers), content)` — `Location` is still present here.
  - **First loss**: `backend/contexts/agents/application/egress.py:107,117` destructure
    `status, _headers, body`, and `:126` builds an `EgressOutcome` that has no headers field
    (`:31-40`). `Location` is unrecoverable from this point on.
  - **Second loss**: `egress.py:38-40` — `ok` is `200 <= self.status < 400`.
  - **Surface**: `backend/contexts/agents/application/runtime/builtin_tools.py:668-671`
    returns `ToolResult(content="HTTP 301\n", is_error=False)`, and the audit row at `:669` is
    written `ok=True`.
  - The Test button repeats the same predicate independently:
    `backend/contexts/agents/application/agent_service.py:1005`, with a comment at
    `:1003-1004` that documents the wrong rule in prose. Its `_probe_function` also discards
    headers (`:988`).

- **Expected.** A redirect is not a successful tool invocation. The model should receive an
  error result naming the redirect target, so it can either retry against a URL that is on the
  allowlist or report the misconfiguration; and the configurator's Test button should report
  the tool as misconfigured rather than healthy.

  **Intent source.** No `[Rxx.yy]` entry classifies tool-call HTTP statuses, so
  `requirements: []` is a positive claim. The expected behaviour rests on internal
  consistency: `follow_redirects=False` makes an empty body certain, and the codebase's own
  provider-call classifier already draws the line at 2xx
  (`backend/contexts/keys/application/router_policy.py:48`,
  `backend/contexts/keys/application/provider_router.py:214,723`). The tool path is the
  outlier.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should the proxy follow redirects, re-running the allowlist per hop? | **No.** Explicitly rejected. | `follow_redirects=False` is load-bearing SSRF policy (§9). Worse, a hop loop replays the caller's injected upstream credential (`app.py:317-343`) to the redirect target — even an allowlisted second host is a different host receiving a credential scoped to the first. Dropping auth on cross-origin hops is the correct behaviour, at which point the redirect commonly 401s and most of the benefit evaporates. Add the per-hop DNS re-screen, re-pin against relative `Location` values, POST-to-GET method rewrite rules for 301/302 versus 307/308, and a rate limiter that counts one call while making up to four requests, and this is a large change in the repo's most security-sensitive file where a subtle mistake is an SSRF hole rather than a bug. |
| Q-2 | Is the root cause the `ok` predicate or the dropped headers? | **Both, and headers are the structural half.** | Fixing only the predicate yields `is_error=True` with content `"HTTP 301\n"` — the model learns it failed but not how to succeed. Threading headers through is what makes the defect recoverable rather than merely correctly labelled. |
| Q-3 | Should `classify_http` (`backend/contexts/keys/application/router_policy.py:26,36-57`) be reused? | **No.** | Wrong abstraction and a layer violation. It is a provider-key *rotation* decision function whose vocabulary is `OK/ROTATE/RETRY/QUOTA/FATAL/ABORT` (`:12-21`) — every non-OK branch is meaningless for a function tool, which has no key group and no rotation. Its signature requires a `RotationPolicy`, a `contexts.keys` domain object (`:9`), so importing it into `contexts/agents` would cross a context boundary the backend import rules forbid. It also does not classify 3xx at all — 301 falls through to the default `ROTATE` (`:57`). Cite it as prior art for the *shape* (a small pure classifier returning a frozen outcome, unit-tested in isolation), not as code to import. |
| Q-4 | Where should the single predicate live, given it currently exists in two places? | On `EgressOutcome` in `backend/contexts/agents/application/egress.py`, consulted by `agent_service._probe_function` rather than re-derived. | This collapses the two copies and makes `egress.py`'s module docstring claim (`:1-11`, that it owns "the status policy" for both callers) true for the first time. Correct reuse is *within* `contexts/agents`, not across to `contexts/keys`. |
| Q-5 | Should 304 Not Modified be special-cased? | No. | Semantically a cache hit and legitimately body-less, but not reachable today: function-tool headers come from static config (`builtin_tools.py:619`) and nothing sends `If-None-Match`. It will classify as an error, which is acceptable. Do not special-case without a concrete need. |
| Q-6 | Does this depend on any open dossier, or overlap the same-day a2a orchestration audit? | No. `depends_on: []`. | Checked against `BOARD.md`. Neither open dossier touches `egress.py`, `builtin_tools.py`'s function-tool branch, or the proxy. The a2a audit's dossiers cover orchestration and the turn loop's locking, not the egress path. |

## 4. Reproduction

**Runtime path.**

1. Add `api.partner.example` to the project's egress allowlist (`mcp_egress_allowlist`,
   checked caller-side at `egress.py:81` and again proxy-side at `app.py:288`).
2. Create an agent tool, `tool_type=local_function`, config
   `{"name": "lookup_order", "http": {"method": "GET", "url": "https://api.partner.example/orders"}}`
   — no trailing slash, where the upstream 301s to `/orders/`.
3. Run a turn in which the model calls `lookup_order`.
4. **Observed**: `builtin_tools.py:671` yields `ToolResult(content="HTTP 301\n", is_error=False)`.
   The model receives a success with no data and answers from nothing. The audit row (`:669`)
   records `ok=True`.

**Test-button path.**

5. Open the agent configurator, Functions tab, press Test on the same tool.
6. `_probe_function` (`agent_service.py:988-1008`) receives 301, computes
   `ok = 200 <= 301 < 400` → `True`, `error=None`.
7. **Observed**: `frontend/src/slices/agents/composables/useToolTest.ts:34-39` fires the
   success toast "Reachable — HTTP 301 in 42ms"
   (`frontend/src/slices/agents/locales/en.json:504`). The configurator declares healthy a
   tool that returns no data at runtime.

**Unit-level equivalent, no network.** Patch
`contexts.agents.application.runtime.builtin_tools.function_egress_allowed` to allow, patch
redis via the existing `_redis_returning(1)` helper
(`backend/tests/unit/test_agents_egress_extraction.py:38-45`), and use a fake proxy returning
`(301, {"location": "https://api.partner.example/orders/"}, b"")`.

## 5. Root Cause Analysis

| # | Link | Evidence |
|---|---|---|
| 1 | The proxy does not follow redirects — deliberate and correct | `backend/services/egress_proxy/app.py:204` |
| 2 | It relays the 3xx status and headers faithfully; `Location` survives the hop-by-hop strip | `app.py:437-438,456-478`, `:383-396` |
| 3 | The infrastructure client returns headers to the application layer | `backend/contexts/agents/infrastructure/egress_client.py:122` |
| 4 | **The application layer discards them** | `backend/contexts/agents/application/egress.py:107,117`, `:126`, dataclass at `:31-40` |
| 5 | **`ok` admits 3xx** | `egress.py:38-40` |
| 6 | The tool surface reports success with an empty body, and audits it as `ok=True` | `builtin_tools.py:668-671` |

**Root cause: links 4 and 5 together.** Link 5 is the proximate cause of the wrong `is_error`
flag; link 4 is what makes the condition unrecoverable rather than merely mislabelled. Link 1
is not a defect and must not be reversed — but it is what turns a 3xx into a *guaranteed*
data-loss case rather than a rare one, which is why the predicate at link 5 is not merely
imprecise but wrong.

**Aggravating factor.** The same predicate exists a second time at `agent_service.py:1005`,
so the authoring-time check agrees with the runtime and neither catches the other's mistake.

**Nothing was persisted incorrectly** beyond audit rows recording `ok=True` for redirecting
invocations; see §9 risk 2.

## 6. Blast Radius and Sibling Suspects

**Consumers of `EgressOutcome.ok`** — exactly one, `builtin_tools.py:668`. **Confirmed
defective.** `contexts/agents/interfaces/facade.py:305` consumes `EgressOutcome` without
`.ok`, mapping to an `EgressResponse` that also carries no headers, so the validator path is
equally `Location`-blind.

| Site | Predicate | Verdict |
|---|---|---|
| `backend/contexts/agents/application/egress.py:40` | `200 <= status < 400` | **Confirmed — the defect.** |
| `backend/contexts/agents/application/agent_service.py:1005` (Test button) | `200 <= status < 400`, with a comment at `:1003-1004` explicitly blessing 3xx | **Confirmed — the same defect, second copy.** |
| `backend/app/workers/tasks/activities.py:77` (webhook validator) | `not (200 <= resp.status < 400)` | **Confirmed, but fails safe.** A 301 passes the status gate, then `result_from_json(b"")` (`backend/contexts/activities/application/validators/base.py:30-35`) raises `ValidatorUnavailable("validator returned non-JSON output")` → `validation_status=error`. No silent success, but the operator is misdiagnosed toward the validator's output format instead of a redirecting URL. Align to 2xx in the same change; low severity. |
| Search adapters — `tavily.py:75`, `serper.py:72`, `google_cse.py:79`, `brave.py:72` | `status >= 400` raises | **Confirmed, fails safe.** A 3xx falls through to `json.loads` on an empty body → `RuntimeError("<provider> returned non-JSON")`. Misdiagnoses rather than silently succeeding. Mitigated because the endpoints are fixed constants, so a redirect is unlikely. Optional tightening. |
| `backend/contexts/keys/application/router_policy.py:48`, `provider_router.py:214,723` | `200 <= status < 300` | **Cleared — already correct.** 3xx falls to `RotationReason.ROTATE`. |
| `builtin_tools.py:212,221,385,586-588` (MCP / code-exec / file) | `res.ok` from a sandbox exit code | **Cleared** — different domain, not a status range. |
| `contexts/agents/interfaces/facade.py:256`, `agent_service.py:1038` (`_probe_mcp`) | passthrough of a sandbox result | **Cleared.** |
| `contexts/keys/application/carry_service.py:218` | `http_status < 200`, a DB filter for "no status recorded" | **Cleared** — unrelated. |
| `app/api/v1/readyz.py:46,61,70`; `contexts/identity/application/auth_service.py:255,551,581,651` | health-check and password-verify `.ok` | **Cleared.** |

**Other code dropping meaningful response headers.**

- `egress.py:107,117` — **confirmed**, the defect. Note it also loses `Retry-After` on a 429
  and `WWW-Authenticate` on a 401, both of which would improve the error messages at
  `builtin_tools.py:671`. Threading headers through fixes all three at once.
- `agent_service.py:988` — **confirmed**, the same loss in the probe.
- All four search adapters discard `_headers` (`tavily.py:68`, `serper.py:62`,
  `google_cse.py:73`, `brave.py:62`). **Cleared for this defect** — those APIs put everything
  in the JSON body — though a dropped `Retry-After` on a provider 429 is a latent quality gap.
  See FU-2.
- `backend/services/egress_proxy/app.py:456-478` relays headers correctly. **Cleared** — the
  proxy is not where anything is lost.

## 7. Fix Design

Two coordinated changes, plus one alignment.

**1. Thread response headers into `EgressOutcome`** (`backend/contexts/agents/application/egress.py`).
Add a headers field to the dataclass (`:31-40`), stop discarding at `:107,117`, and populate
at `:126`. Keep the dataclass `frozen=True, slots=True` as it is today — use an immutable
mapping (a normalised frozen dict, or `tuple[tuple[str, str], ...]`) rather than a mutable
`dict`, consistent with `ToolProbeResult`
(`backend/contexts/agents/domain/models.py:210-217`) and `ErrorOutcome`
(`router_policy.py:29-33`). Header lookup must be case-insensitive: `egress_client.py:122`
returns `dict(resp.headers)` and httpx lowercases keys on that conversion, but the port
protocol at `backend/contexts/agents/application/mcp_ports.py:172` promises only
`dict[str, str]`, so normalise defensively rather than relying on the current implementation.

**2. Narrow the predicate and surface the target.** `ok` becomes `200 <= status < 300`, with a
redirect accessor exposing the `Location`. `builtin_tools.py:668-671` composes the message the
model reads — something on the order of `HTTP 301 — redirect to <Location>; retry with that
URL if the host is on the allowlist` — and passes `ok=False` to `_audit_tool_invoke` at
`:669`. Preserve the existing split: policy and classification live in `egress.py`, the
sentence the model reads is composed caller-side (`builtin_tools.py:650-666` is the
established shape). If a distinct `redirect` outcome needs modelling, mirror
`EgressBlocked.kind` (`egress.py:28,43-54`), which is already the codebase's pattern for "the
caller renders its own message from a classified reason".

**3. Make the probe consult the same predicate.** `agent_service._probe_function` (`:1005`)
stops re-deriving the range and consults the `EgressOutcome` classification, stops discarding
headers at `:988`, and reports `error` naming the redirect target. Correct the comment at
`:1003-1004` and the module docstring of `backend/tests/unit/test_function_probe.py:4-5`,
both of which currently state the wrong rule in prose. No frontend change is required:
`useToolTest.ts:48-49` already renders `res.error`, and `AgentToolTestOut`
(`backend/app/api/v1/agents.py:744-750`) already carries `status` and `error`.

**Alignment, same change:** narrow `activities.py:77` to 2xx so a redirecting webhook
validator is diagnosed as a redirect rather than as non-JSON output. Same terminal state,
better message.

**Why this corrects rather than masks.** The symptom is a mislabelled result; the disease is
that the application layer threw away the only field that makes a 3xx actionable. Narrowing
the predicate alone would leave the model told "this failed" with no way to succeed. Threading
`Location` through makes the redirect self-correcting: the model may retry, and that retry
re-enters `perform_egress_request` and re-runs `function_egress_allowed` (`egress.py:81`), so
a redirect to a non-allowlisted host is refused rather than followed. The security property is
preserved *because* the model, not the proxy, does the retrying.

**Data repair: none.** The only incorrect persisted data is audit rows recording `ok=True` for
redirecting invocations. They are historically accurate records of what the system decided at
the time and should not be rewritten.

## 8. Regression Test Plan

**Anchor:** `backend/tests/unit/test_agents_egress_extraction.py`, which today exercises only
200 (`_FakeProxy` at `:33-35`) and 201 (`:141`). No 3xx appears anywhere in the suite.

**Fixture prerequisite.** `_FakeProxy` (`:29-36`) returns a hardcoded `200, {}, b"ok"`; it
needs constructor parameters for status and headers before any of the below can be written.

**The failing test comes first** — `test_outcome_carries_response_headers`: the proxy returns
`(301, {"location": "https://x/y/"}, b"")` and the resulting outcome exposes that location.
**Fails today** with `AttributeError`: `EgressOutcome` has no headers field (`egress.py:31-40`).

Then:

| Test | Assertion | Why it fails today |
|---|---|---|
| `test_3xx_is_not_ok` | `EgressOutcome(status=301, …).ok is False` | `egress.py:40` returns `True` for 301 |
| `test_header_lookup_is_case_insensitive` | proxy returns `{"Location": …}`; lookup by `"location"` succeeds | no accessor exists |
| `test_2xx_still_ok` (guard) | 200 and 204 remain `ok` | passes today; pins against over-narrowing |
| `test_4xx_5xx_not_ok` (guard) | 404 and 500 remain not-`ok` | passes today |

**New — function-tool result shaping.** No existing file covers `_build_function_tool`'s
result mapping. Either a new `backend/tests/unit/test_function_tool_result.py` or an added
class in the anchor file:

| Test | Assertion | Why it fails today |
|---|---|---|
| `test_redirect_is_an_error_result` | invoking against a 301 yields `is_error=True` | `builtin_tools.py:671` yields `is_error=False` |
| `test_redirect_message_names_the_location` | the target URL appears in `result.content` | `Location` never reaches `builtin_tools.py` |
| `test_redirect_audits_as_failure` | `_audit_tool_invoke` receives `ok=False` | `builtin_tools.py:669` passes `ok=True` |

**`backend/tests/unit/test_function_probe.py`** — `_FakeProxy` at `:63-68` already takes a
status, so only a headers parameter is needed:

| Test | Assertion | Why it fails today |
|---|---|---|
| `test_3xx_is_a_failure` | 301 → `res.ok is False`, `res.status == 301` | `agent_service.py:1005` returns `ok=True` |
| `test_3xx_error_names_redirect_target` | the target appears in `res.error` | `res.error` is `None` when `ok` (`:1007`), and headers are discarded at `:988` |
| existing `test_2xx_is_a_pass` (`:89`) | unchanged | guard |

**Activities validator** — if `activities.py:77` is narrowed, a 301 must raise
`ValidatorUnavailable` with a message naming the status rather than falling through to
`result_from_json`. Fails today because the status gate admits 301.

## 9. Risks and Rollback

**The central question: could narrowing `ok` break a function tool that legitimately returns
3xx? No — "currently working" is not achievable for a 3xx under this proxy.**
`follow_redirects=False` (`app.py:204`) makes a 3xx body empty by construction, so any tool
whose upstream redirects is today returning a success with no payload. There is no
configuration in which that is useful to the model. Narrowing converts a silent wrong answer
into a visible, actionable error; it cannot break a tool that was actually delivering data.

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Behaviour-change visibility: tools that silently returned nothing now error, and green Test buttons go red | medium | low | The message names the `Location` and the remedy is a one-field URL edit, surfaced at authoring time by change 3. Worth a release note. |
| Audit-trail discontinuity: `agent_tool.invoke` rows flip from `ok=True` to `ok=False` at the deploy boundary, producing a step in any tool-success chart | high | very low | Acceptable — the new numbers are the true ones. Do not rewrite history. |
| A malformed upstream 3xxes without `Location` | low | low | Message composition must handle `None` without raising; "HTTP 304, no redirect target" is fine. |
| Cross-context ripple if `activities.py:77` is narrowed in the same change | low | very low | Same terminal state (`validation_status=error`), better diagnosis. |

**Security — what must not be weakened.** The recommended fix touches none of items 1–5
below, which is its main virtue:

1. **`follow_redirects=False` (`app.py:204`) stays** — it is the guarantee that every byte the
   proxy fetches came from a host that passed both `is_blocked_ip` over all resolved addresses
   (`app.py:271-285`) and the per-project allowlist (`:287-302`).
2. **The pinning invariant stays** (`app.py:265-270`, `_pin_url` at `:170-180`) — the socket
   must land on a pre-screened IP literal with `Host`/`sni_hostname` carried separately. No
   code path may let httpx resolve a hostname itself.
3. **The all-addresses block rule stays** (`app.py:274`,
   `backend/services/egress_proxy/ip_policy.py:65-108`) — the anti-rebinding posture
   documented at `ip_policy.py:23-24`. Do not relax it opportunistically while in this file.
   (The audit routed the question of whether that posture should be revisited to
   `check-security` as FU-2 of `docs/audits/2026-07-22-agent-config-runtime/findings.md`.)
4. **Upstream-auth injection must never cross an origin** (`app.py:317-343`) — the strongest
   single argument against Q-1's rejected option.
5. **The allowlist check stays a pre-condition of every outbound connection**, caller-side
   (`egress.py:81`) and proxy-side (`app.py:288`) both — defence in depth; keep both.
6. **`Location` is untrusted input.** It must be clipped (`clip_tool_output` already wraps the
   content at `builtin_tools.py:671`) and treated as data. It is safe to surface because a
   model-driven retry re-enters the allowlist check — **never** add a path that retries the
   `Location` automatically while skipping that check.
7. **Do not log `Location` alongside auth material.** `app.py:445-454` already logs at DEBUG
   with `_truncate`/`mask_str`; add no unmasked header dumps.

**Rollback.** A pure application-layer change: one dataclass field, three destructures, two
predicates, two message strings. No migration, no proxy change, no API schema change, no
required frontend change. Revert is a single `git revert` with no data cleanup.

## 10. Acceptance Criteria

- [ ] AC-1: `test_outcome_carries_response_headers` (§8) fails against current code and passes
      after the fix.
- [ ] AC-2: a function tool whose upstream returns 3xx yields `is_error=True`, and the result
      content names the redirect target.
- [ ] AC-3: the corresponding audit row records `ok=False`.
- [ ] AC-4: the configurator's Test button reports a redirecting URL as a failure, with the
      target in the error message.
- [ ] AC-5: 2xx behaviour is unchanged — the existing 200/201 tests and
      `test_function_probe.py::test_2xx_is_a_pass` pass without modification.
- [ ] AC-6: the status predicate exists in exactly one place; `_probe_function` consults it
      rather than re-deriving the range.
- [ ] AC-7: `backend/services/egress_proxy/` is unmodified — no change to
      `follow_redirects`, to IP pinning, or to the allowlist check.
- [ ] AC-8: the prose that states the wrong rule is corrected —
      `agent_service.py:1003-1004` and `backend/tests/unit/test_function_probe.py:4-5`.
- [ ] AC-9: `pytest -q`, `ruff check .`, `ruff format --check .` and `mypy .` pass in
      `backend/`.

## 11. SRS Delta

None. No `[Rxx.yy]` entry classifies tool-call HTTP statuses; this restores internal
consistency with the provider-call path, which already draws the line at 2xx. See FU-1.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- **FU-1** — No SRS entry defines what constitutes a successful function-tool invocation. The
  policy currently lives in two predicates and a comment. One `[Rxx.yy]` entry would give
  future audits something to judge against.
- **FU-2** — All four search adapters discard response headers (`tavily.py:68`,
  `serper.py:62`, `google_cse.py:73`, `brave.py:62`), so a provider's `Retry-After` on a 429
  is lost. Cleared for this defect (those APIs carry everything in the JSON body) but a latent
  quality gap in rate-limit handling.
- **FU-3** — The search adapters' `status >= 400` predicate lets a 3xx fall through to
  `json.loads` on an empty body, producing "returned non-JSON". Fails safe but misdiagnoses.
  Unlikely in practice since the endpoints are fixed constants; tighten opportunistically.
- **FU-4** — `egress.py`'s module docstring (`:1-11`) claims it owns the status policy for
  both callers, which was untrue before this fix. Verify the claim holds after, and treat a
  future divergence as a docstring bug rather than an acceptable duplication.
</content>
