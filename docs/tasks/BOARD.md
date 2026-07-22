# Task Board

Derived view over every dossier under `docs/tasks/` that is not
`implemented`/`superseded`/`abandoned`, grouped by `depends_on` + `status` per the rules
in `README.md`. If this file and a dossier's own frontmatter disagree, the frontmatter
wins — this is a cache, not a second source of truth. Maintained by `/spec` (adds a row
on dossier creation) and `/build` (moves a row on every status change).

Backfilled 2026-07-20 for the dossiers active at that date; the other ~80 dossiers under
`docs/tasks/` were already `implemented`/`superseded` and are intentionally not listed
here (see README.md's Dependencies and sequencing section for why untouched history
doesn't need a `depends_on` backfill).

## Ready now

Nothing blocking; these can start in any order relative to each other, including in
parallel.

- `2026-07-22-egress-redirect-classification` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-10 (major): a 3xx from a function tool is
  delivered to the model as a successful empty result, because the proxy deliberately does not
  follow redirects and the caller drops the `Location` header. Application-layer only; the
  egress proxy is explicitly not modified.
- `2026-07-22-model-hint-provider-routing` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-2 (critical): an agent's `model_hint` does
  not constrain provider routing, so a mixed-provider key group silently runs the agent on a
  different provider and model. Touches `contexts/keys` routing, `turn_engine.py` model
  resolution and payload construction, and `summariser.py`; no migration.
- `2026-07-22-web-search-cache-project-scoping` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-1 (critical): the `web_search` result
  cache is keyed without tenant identity, so one project's search results are served to
  another. Confined to `contexts/agents/application/tools/web_search.py` plus its unit test;
  no migration.
- `2026-07-07-graphrag-two-axis-redesign` (feature, approved) — `depends_on: []`. This is
  a blueprint dossier: approval authorizes the target design, and its phases are meant to
  become separate `/build` dossiers (see its own §1). Open question: `docs/tasks/2026-07-07-graphrag-phase0..4b-*`
  already exist and are all `status: implemented` with overlapping `[Rxx.yy]` coverage —
  worth confirming with the user whether this blueprint's remaining scope is still live or
  its status is simply stale, before treating it as unblocked work.

## Blocked

Nothing blocked.

## In progress

- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.
