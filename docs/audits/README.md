# Audits

Investigation records produced by `/audit` — one folder per audit, each holding a
`findings.md`. Audits produce knowledge, not code changes; the actionable work they
surface becomes task dossiers under `docs/tasks/`, linked from each audit's hand-off
section.

The dossier rules (frontmatter, status lifecycle, F-n/FU-n numbering, evidence standard)
are shared with tasks and live in one place: [`docs/tasks/README.md`](../tasks/README.md).

```
docs/audits/YYYY-MM-DD-<slug>/findings.md   # type: audit; status: draft | reviewed | closed
```
