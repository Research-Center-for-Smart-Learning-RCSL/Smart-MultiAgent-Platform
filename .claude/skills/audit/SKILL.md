---
name: audit
description: Hunt for functional bugs — behavior that deviates from documented intent — in a chosen area of the codebase, adversarially verify every candidate finding, and produce a findings dossier (docs/audits/) whose entries convert into bugfix specs. Use when the user wants to audit, sweep, or search an area for bugs or defects ("audit the conversation slice", "are there functional bugs in workflow execution?", "check the keys context for logic errors"). Not for structural quality (check-quality), vulnerabilities (check-security), or diff review (code-review) — this skill judges behavior against intent, area-wide.
---

## Purpose

Investigation only — this skill changes no code. The deliverable is `findings.md` in an
audit dossier under `docs/audits/`: a verified, evidence-backed list of functional
defects, each convertible into a bugfix spec. The dossier contract in
`docs/tasks/README.md` governs the output (it covers both the audits and tasks trees) —
read it first.

**Boundary with the other review skills**: this skill answers "does the behavior match
the intent?" area-wide. Structural quality belongs to `check-quality`, vulnerabilities
to `check-security`, reviewing a diff to `/code-review`. If a sweep surfaces those,
record them as FU-n and route them — don't let them dilute the findings list.

## Step 1 — Confirm scope

Agree with the user before spending analysis effort:

- **Area** — which contexts, slices, or flows. "The whole codebase" is a valid answer
  but should be a deliberate one; offer to split it into per-area audits.
- **Intent sources** — what defines correct behavior here: `REQUIREMENTS.md` `[Rxx.yy]`
  entries, approved spec dossiers, schema docs. An audit without intent sources can only
  find internal inconsistencies — say so if sources are thin.
- **Depth** — quick sweep vs. thorough (affects fan-out size and verification rounds).

## Step 2 — Investigate

Fan out read-only subagents, each with a distinct lens — diversity of angle finds what
redundancy cannot. Lenses that fit this codebase:

- **State and lifecycle** — status transitions, orphaned states, resume/cancel paths.
- **Boundary inputs** — empty, maximal, malformed, duplicate; pagination edges.
- **Concurrency and async** — races between WebSocket events and REST state, missing
  awaits, out-of-order delivery, worker retries.
- **Isolation as correctness** — cross-tenant / cross-room / cross-project data flow
  (the functional side of the recent observer-agent leak class).
- **Event and notification flow** — emitted but unconsumed events, missed emissions,
  desync between backend state and frontend cache.
- **Error paths** — swallowed failures, partial writes, cleanup that doesn't run.

Each agent returns candidate findings with `path:line` evidence and a concrete
failure scenario (inputs/state that trigger the wrong behavior). Candidates without a
failure scenario are hunches, not findings.

## Step 3 — Adversarial verification

Plausible-but-wrong findings are this workflow's biggest failure mode. For every
candidate, run an independent verification pass whose explicit job is to **refute** it:
re-read the code paths, look for the guard the finder missed, check whether tests
already pin the behavior, trace the alleged failure scenario step by step.

- Refuted → discard (keep a one-line note if the refutation was interesting).
- Survives with a fully traced failure scenario → **confirmed**.
- Survives but the scenario couldn't be fully traced → **plausible**, marked as such.

## Step 4 — Write findings.md

Create `docs/audits/YYYY-MM-DD-<slug>/findings.md` with `type: audit`, `status: draft`.
Order findings by severity. Each entry:

```markdown
## F-1: <one-line defect statement>

- **Severity**: critical | major | minor
- **Verdict**: confirmed | plausible
- **Evidence**: path:line, ...
- **Failure scenario**: concrete inputs/state → wrong outcome
- **Blast radius**: what/who is affected
- **Intent source**: [Rxx.yy] or spec dossier the behavior violates
```

Also state what was covered and what was not (areas skipped, depth limits) — a findings
list without coverage boundaries reads as "everything else is clean" when it isn't.

## Step 5 — Hand off

Present the findings summary. For each finding the user selects for fixing, create a
bugfix dossier via `/spec` (bugfix mode) — the finding pre-fills Observed vs Expected,
evidence, and reproduction, so the spec step is fast. Set `findings.md` to
`status: reviewed` once the user has triaged, and `closed` when every selected finding
has a linked dossier.

## Step 6 — Commit

Commit `findings.md` following the CLAUDE.md commit discipline: English message
(`docs(review): ...`), no co-author trailer, and stage only the audit dossier folder —
never `git add -A`/`.`/`-a`, since the tree may hold unrelated in-progress work. Commit
the draft when written and again after triage flips the status. This skill changes no
source code, so these are the only commits it makes. Do not push without explicit user
confirmation.
