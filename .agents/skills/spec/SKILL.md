---
name: spec
description: Turn a feature request, bug report, or refactoring idea into an approved task dossier (docs/tasks/) with verified codebase analysis, numbered acceptance criteria, and recorded design decisions. Use whenever the user wants to plan, scope, analyze, or write a spec/design/requirements document for a change before implementing it — including "let's plan X", "analyze this bug before we fix it", "how should we build X", "write a spec for X", or preparing work to hand to /build. Do not use for implementation itself (/build) or open-ended bug hunting (/audit).
---

## Purpose

Produce a task dossier that a different session — or a different engineer — could
implement without re-deriving the analysis. The dossier is the deliverable; code changes
are out of scope for this skill.

Read `docs/tasks/README.md` first. It defines the dossier contract this skill writes
against: folder layout, frontmatter, status lifecycle, numbering (AC-n / Q-n / D-n /
FU-n), the evidence standard, and the SRS Delta protocol. Everything below assumes it.

## Step 1 — Classify the task

Three types, each with its own template:

| Type | Template | Core question the spec answers |
|---|---|---|
| feature | `templates/feature.md` | What should the system do that it doesn't today? |
| bugfix | `templates/bugfix.md` | Why does behavior deviate from intent, and what restores it? |
| refactor | `templates/refactor.md` | How does structure improve while behavior stays identical? |

If the user's request doesn't clearly fit one, ask — the templates diverge enough that
guessing wrong wastes the whole analysis. Requests to "find bugs" in an area belong to
`/audit`, not here; a single already-observed bug belongs here as a bugfix.

## Step 2 — Clarify requirements

Use structured multiple-choice questions with trade-offs stated per option
(AskUserQuestion), not open-ended essay questions — the user picks a direction faster
and the trade-off reasoning gets captured for free. Record every question and the chosen
answer as Q-n in the dossier: six months later, "why is it built this way" is answered by
the Q log, not by archaeology.

Stop asking when — and only when — all three are confirmed by the user:

1. **Goals** — what must be true when this is done.
2. **Non-goals** — what is explicitly out of scope. This is the only fence against scope
   creep during implementation, so push for real exclusions, not filler.
3. **Acceptance criteria** — observable, testable statements, numbered AC-1, AC-2, ...
   Each AC must be verifiable by a test or a concrete manual check; "works correctly" is
   not an AC.

Don't front-load every question. Ask what you need to scope the analysis, run Step 3,
then come back with sharper questions if the code contradicts an assumption.

## Step 3 — Analyze the codebase

Fan out Explore subagents for the reading; keep the main context for synthesis and
writing. Typical fan-out: one agent per affected area (backend context, frontend slice,
deploy config), plus one tracing the end-to-end data flow for the touched behavior.

Apply the evidence standard from the contract: every claim cites `path:line`, nothing
speculative, citations instead of pasted code. Also check intent sources — does
`REQUIREMENTS.md` already constrain this area? List the relevant `[Rxx.yy]` IDs in the
frontmatter.

## Step 4 — Quality and security lens

The spec must set up the implementation to leave the codebase better, not just bigger.
Using the dimensions from the `check-quality` skill as a checklist against the touched
area, write three things into the spec:

1. **Existing debt** — quality problems already present in the files this task touches,
   so the implementer knows what not to imitate (and what not to silently "fix" —
   record it, decide explicitly).
2. **Patterns to follow** — the layer boundaries (SoC), naming, and idioms this change
   must respect, with pointers to exemplar files.
3. **Reuse inventory** — existing helpers, composables, shared-kernel utilities, and UI
   components the implementation should use instead of re-inventing. This list is the
   single most effective duplicate-code prevention we have; be thorough.

If the task touches auth, provider keys, tenant boundaries, WebSocket, file upload,
any user-input processing, or agent/LLM prompt and tool surfaces, add a Security
Considerations section informed by the `check-security` dimensions for that surface.

## Step 5 — Write the dossier

Create `docs/tasks/YYYY-MM-DD-<slug>/spec.md` from the matching template with
`status: draft`. Fill every section; if a section is genuinely empty (e.g., SRS Delta for
a bugfix), say "None" rather than deleting it — an absent section is ambiguous, an
explicit "None" is a statement.

## Step 6 — Approval gate

Present the user a condensed summary: goals, non-goals, the chosen design and what it
was chosen over, ACs, risks, and the SRS Delta if any. On explicit approval:

1. Apply the SRS Delta to `REQUIREMENTS.md` verbatim (feature specs; see the contract's
   SRS Delta protocol).
2. Flip `status: draft` to `status: approved`.

If the user requests changes, revise and re-present. Never flip the status yourself
without the user's explicit approval — `/build` refuses draft dossiers by design, and
that gate only means something if this skill honors it.
