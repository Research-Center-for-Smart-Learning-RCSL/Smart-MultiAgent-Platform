# Phase F — Local Shell (frontend stub)

**Goal.** Show **Local Shell** in the Local group as a visible, clearly "not yet
available" capability. Clicking it surfaces a "coming soon" affordance. The
backend neither builds nor accepts `local_shell` this round.

**Size.** S
**Depends on.** E (the Local group / card scaffolding lands with the Functions
work) and A (the enum already includes `local_shell` for forward-compat).
**Scope guard.** No sandbox, no executor, no migration, no API acceptance.

## F.1 Backend posture — reject, don't implement — **CODE** — XS

Already specified in A.5/A.7: `add_tool` rejects `tool_type:"local_shell"` with
`422 tool-not-available`, and `build_agent_tools` skips any `local_shell` row
(none can exist). Add/confirm a unit test asserting the rejection so a future
implementer has a failing-by-design marker to flip.

**Exit criteria.** `POST /tools {tool_type:"local_shell"}` → 422
`tool-not-available`; `build_agent_tools` ignores the type.

## F.2 Frontend card — "coming soon" — **CODE** — S

In `AgentToolsView` Local group, the **Local Shell** card:

- Rendered with the same card chrome as Functions. Use existing shared components
  only — **there is no `SPopover`**; use `SBadge` for a "Coming soon" badge next to
  the label and a **disabled** `SToggle` (the `disabled` prop is already used in the
  current built-in toggle list). Do not invent a new component.
- On click of the card's "Learn more" affordance (a plain `SButton` link), show the
  existing toast (`useToast`, already imported in the current MCP view) with
  `$t('agents.tools.localShell.comingSoon')`. The disabled toggle itself does
  nothing on click; a `title`/`aria-label` carries the same text for a11y.
- No network call is made; nothing is persisted (test: spy on `agentsApi.addTool`
  /`patchTool` and assert call count 0).
- Description copy explains the planned behavior (server-side bash in the gVisor
  sandbox, like Code Interpreter but shell) so the intent is clear.

**Exit criteria.** Card visible in the Local group; toggle disabled; click shows
the coming-soon message; no API request fires (assert in the Playwright/Vitest
test).

## F.3 i18n — **CODE** — XS

Add `agents.tools.localShell.{label,description,comingSoon,badge}` to en + zh-TW.
Escape any literal `@`.

**Exit criteria.** Both locales present; key-parity test green.

## F.∞ Phase gate

- [ ] Backend rejects `local_shell` create (test present as the future marker).
- [ ] Card visible, disabled, "coming soon" on click, zero network calls.
- [ ] i18n parity.
- [ ] `00-overview.md` §0.6: F = done.

## Future work (out of scope — recorded for the next round)

When Local Shell is built: a digest-pinned shell sandbox image under the existing
gVisor policy (mirror `smap/code-exec`), a `run_shell` runner method, a
`_build_shell_tool` builder, command/output caps, and an explicit per-agent
enable. It shares the per-agent `/workspace` volume with Code Interpreter, so the
Phase D uploads are already available to it. No data-model change is needed — the
`local_shell` enum value and `agent_tools` row shape already exist.
