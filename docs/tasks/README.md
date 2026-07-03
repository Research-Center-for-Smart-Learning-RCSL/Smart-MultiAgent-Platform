# Task Dossier Contract

This directory holds one folder per unit of work: a feature, a bugfix, a refactor, or an
audit. A dossier is the single source of truth for that task — what was asked, what was
decided, what was done, and what was deliberately left out. The `/spec`, `/build`, and
`/audit` skills read and write dossiers according to this contract; humans reviewing a PR
read the same files. Keep dossiers factual, not aspirational: if the implementation
deviated from the plan, the dossier says so.

## Layout

```
docs/tasks/
  README.md                    this contract
  YYYY-MM-DD-<slug>/           one folder per task; date is the creation date
    spec.md                    feature / bugfix / refactor tasks
    findings.md                audit tasks
```

Slugs are short kebab-case English (`2026-07-03-agent-avatar-upload`). Everything under
`docs/` is English-only; dossiers are no exception.

## spec.md frontmatter

```yaml
---
type: feature | bugfix | refactor
status: draft | approved | in-progress | implemented | superseded | abandoned
created: YYYY-MM-DD
requirements: [R12.03, R07.10]    # related SRS IDs; empty list if none
supersedes: 2026-06-01-old-slug   # optional, only when replacing a dossier
---
```

`findings.md` uses `type: audit` and `status: draft | reviewed | closed`.

## Status lifecycle

| Transition | Performed by |
|---|---|
| (new) → `draft` | `/spec` (or `/audit` for findings) on creation |
| `draft` → `approved` | The user, explicitly. `/spec` applies the SRS Delta to `REQUIREMENTS.md` at this moment, never before. |
| `approved` → `in-progress` | `/build` when implementation starts |
| `in-progress` → `implemented` | `/build` only after the full Definition of Done passes |
| any → `superseded` | The user; the new dossier links back via `supersedes` |
| any → `abandoned` | The user |

`/build` must refuse to implement a `draft` dossier — the approval gate is the whole
point of having one.

## Numbering conventions

These mirror the project-wide `[Rxx.yy]` / `(Q##)` traceability scheme at task scale.
Numbered items are stable identifiers: never renumber, only append.

- **AC-n** — acceptance criteria, written as checkboxes (`- [ ] AC-1: ...`). `/build`
  checks them off as each is verified, which is also how a resumed session knows where
  work stopped.
- **Q-n** — clarification questions asked during `/spec`, recorded with the chosen
  answer so the reasoning survives the conversation.
- **F-n** — audit findings in `findings.md`.
- **D-n** — deviations: places where the implementation departed from the approved spec,
  appended by `/build` with the reason.
- **FU-n** — follow-ups: out-of-scope discoveries recorded for later, explicitly not
  fixed in this task.

## Evidence standard

- Every claim about existing code cites `path:line`. A claim without a citation is an
  open question, not a fact — label it as such.
- No speculative wording ("probably", "should be", "likely"). Verify it or move it to
  Open Questions.
- Cite code locations instead of pasting code. Snippets longer than ~10 lines almost
  never belong in a dossier; they go stale the moment the file changes.
- Detailed but not redundant: include what changes a decision or an implementation;
  drop what doesn't.

## SRS Delta protocol

`REQUIREMENTS.md` is the authoritative SRS. A dossier never quietly invents requirements
beside it. A feature spec drafts its new or amended `[Rxx.yy]` entries verbatim in its
"SRS Delta" section; when the user approves the spec, the delta is applied to
`REQUIREMENTS.md` in the same step, so the SRS is already current when implementation
begins. Bugfix and refactor dossiers usually carry an empty delta — they restore or
preserve documented behavior rather than define new behavior.

## Write-back rule

After implementation, the dossier must still tell the truth. `/build` appends D-n entries
for every deviation and FU-n entries for every out-of-scope discovery. A dossier whose
Deviation Log is empty asserts that the code matches the spec exactly.
