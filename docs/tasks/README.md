# Task Dossier Contract

A dossier is the single source of truth for one unit of work — what was asked, what was
decided, what was done, and what was deliberately left out. The `/spec`, `/build`, and
`/audit` skills read and write dossiers according to this contract; humans reviewing a PR
read the same files. Keep dossiers factual, not aspirational: if the implementation
deviated from the plan, the dossier says so.

## Two trees: audits vs tasks

Findings and specs are different kinds of artifact with different lifecycles, so they
live in separate trees. An **audit** is an investigation record — it produces knowledge,
not a change. A **task** is actionable, buildable work. Audits spawn tasks; the two link
across trees but never share a folder, so a path alone tells you which you are looking at.

```
docs/
  audits/
    <this contract applies>       audits reference docs/tasks/README.md for the shared rules
    YYYY-MM-DD-<slug>/
      findings.md                 /audit output — type: audit
  tasks/
    README.md                     this contract
    YYYY-MM-DD-<slug>/
      spec.md                     /spec + /build work — type: feature | bugfix | refactor
```

- `/audit` writes only under `docs/audits/`. `findings.md` links the task slugs it spawns
  in its hand-off section.
- `/spec` and `/build` operate only under `docs/tasks/`. A spec born from an audit cites
  that audit's `docs/audits/<slug>/findings.md` in its summary.

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
depends_on: []                    # slugs that must be `implemented` before this starts
---
```

## findings.md frontmatter

```yaml
---
type: audit
status: draft | reviewed | closed
created: YYYY-MM-DD
requirements: [R12.03, R07.10]    # SRS IDs that served as intent sources; empty if none
---
```

`requirements` here means something different from its meaning in a spec: it lists the
intent sources the audit judged behavior *against*, not requirements the work implements.
An empty list is a statement that the audit had no documented intent to check against —
which bounds what its findings can mean, so it belongs in the Scope section too.

There is deliberately no `depends_on` and no `supersedes`. An audit produces knowledge,
not a change; nothing sequences against it, and a later audit of the same area does not
invalidate an earlier one's findings — it is simply a second observation.

The section structure `/audit` writes is fixed by its `templates/findings.md`. Two parts
of it are load-bearing for this contract: the Coverage section (findings without stated
boundaries read as "everything else is clean") and the Hand-off table, which is where the
task slugs an audit spawns get linked, including the findings the user declined.

## Dependencies and sequencing

A pile of dossiers with only a filename each does not tell a reader which to build first
or which can run side by side. Two mechanisms close that gap; neither requires opening
every `spec.md` to find out.

**`depends_on`** is a list of task slugs (folder names under `docs/tasks/`) that must
reach `status: implemented` before this dossier may move `approved` → `in-progress`.
`/build` enforces this as a hard gate (see its Step 1). A slug belongs in `depends_on`
for either reason:

- **Logical prerequisite** — this task's fix or feature only makes sense once the other
  lands (e.g. it references code the other dossier introduces).
- **Overlap prerequisite** — no logical ordering requirement, but both dossiers touch the
  same files/lines closely enough that building them concurrently would produce
  conflicting diffs. Building them serially avoids the conflict even though either could
  technically go first.

Either reason is valid; `/spec` proposes each dependency as a Clarifications entry (Q-n)
so the reason lives in that row's Rationale column, the same place every other spec
decision already gets recorded — no separate mechanism needed. An empty `depends_on` is
a positive claim — "nothing known blocks this" — not an unfilled field.

**Sequential naming.** When a new dossier continues a multi-step initiative that already
has dossiers in `docs/tasks/` (compare `graphrag-phase0-engine-cleanup`,
`-phase1-decouple-owner`, `-phase2a-builder-hardening`, `-phase2b-...`, `-phase3-...`,
`-phase4a-...`, `-phase4b-...`), `/spec` names the new slug
`<initiative>-phase<N>-<detail>` (or `-step<N>-` if the initiative already uses that
word) so that lexical sort of `docs/tasks/` — which is also chronological sort, since the
date prefix comes first — matches build order within that initiative. This only encodes
order *inside one initiative's naming family*; it says nothing about cross-initiative
dependencies or which initiatives can run in parallel — that is what `depends_on` and
`BOARD.md` are for. Existing dossiers are never renamed retroactively: citations
elsewhere (`supersedes`, other dossiers' prose, code comments) point at the folder name,
and renaming breaks them.

## Status lifecycle

**spec.md**

| Transition | Performed by |
|---|---|
| (new) → `draft` | `/spec` on creation |
| `draft` → `approved` | The user, explicitly. `/spec` applies the SRS Delta to `REQUIREMENTS.md` at this moment, never before. |
| `approved` → `in-progress` | `/build` when implementation starts |
| `in-progress` → `implemented` | `/build` only after the full Definition of Done passes |
| any → `superseded` | The user; the new dossier links back via `supersedes` |
| any → `abandoned` | The user |

`/build` must refuse to implement a `draft` dossier — the approval gate is the whole
point of having one.

**findings.md**

| Transition | Performed by |
|---|---|
| (new) → `draft` | `/audit` on creation |
| `draft` → `reviewed` | `/audit` once the user has triaged every finding |
| `reviewed` → `closed` | `/audit` once every finding selected for fixing has a linked task dossier in the Hand-off table |

`closed` says the hand-off is complete, not that the defects are fixed — the linked
dossiers own that. An audit whose findings were all declined still reaches `closed`, with
the declines recorded.

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

## docs/tasks/BOARD.md — the sequencing index

`BOARD.md` is a derived view over `docs/tasks/` only — every non-`implemented`/
`superseded`/`abandoned` `spec.md`, grouped by its `depends_on` and `status`. Audits never
appear on it: the board answers "what can I build next", and an audit is not buildable
work. A finding reaches the board only once it becomes a task dossier, at which point it
is that dossier's row like any other.

The groups:

- **Ready now** — `approved` or `draft` dossiers whose every `depends_on` entry is
  already `implemented` (or the list is empty). These can start in any order relative to
  each other, including in parallel.
- **Blocked** — dossiers with at least one unmet `depends_on` entry, listed with what
  they are waiting on.
- **In progress** — `status: in-progress`.

It exists so a reader can answer "what can I pick up right now, and what can run
alongside it" from one file instead of opening every `spec.md`. If `BOARD.md` and a
dossier's own frontmatter ever disagree, the frontmatter wins — `BOARD.md` is a cache,
not a second source of truth.

`/spec` adds a row when it writes a new dossier (its Step 7). `/build` moves a task
between sections whenever it changes `status` (its Step 3 and Step 6) — most notably,
finishing a task can move other dossiers from Blocked to Ready, which `/build` calls out
to the user rather than leaving for them to notice on the next read.

## Write-back rule

After implementation, the dossier must still tell the truth. `/build` appends D-n entries
for every deviation and FU-n entries for every out-of-scope discovery. A dossier whose
Deviation Log is empty asserts that the code matches the spec exactly.
