# Dependency Holds

Dependencies deliberately kept below their latest version, with the evidence and
the condition for releasing the hold.

Without this file the investigation gets re-derived from scratch every time, or
the bump lands and breaks CI again. Each entry must carry a reproduction, so the
next person can re-check in minutes rather than re-bisect.

Every entry has a matching `ignore` rule in `.github/dependabot.yml`. A held
package that is still proposed does not just cost a review — inside a group it
takes the whole group's PR red, so unrelated bumps cannot land either (this
sank #108 and #130).

Delete an entry when its hold is released, and delete its ignore rule with it —
a stale entry is worse than none.

## msw — held at 2.7.3 (latest 2.15.0)

**Logged:** 2026-08-07 · **Blocks:** the frontend-minor-and-patch group (#108, #130)

msw 2.15.0 aborts the Node process during test teardown. Three vitest workers
die per run, taking 26 tests with them, and **no assertion failure is produced**
— the suite reports `169 passed (172)` with nothing pointing at a cause:

```
linux:   node: ../deps/uv/src/unix/stream.c:455: uv__stream_destroy:
         Assertion `!uv__io_active(&stream->io_watcher, POLLIN | POLLOUT)' failed.
windows: Assertion failed: handle->reqs_pending == 0, file uv/src/win/tcp.c, line 238
```

Its interceptor destroys a socket while the IO watcher is still armed, which
libuv asserts on rather than tolerates. Not platform-specific — both CI (Linux)
and a Windows dev host reproduce it identically.

**Reproduction:** set `msw` to `2.15.0` in `frontend/package.json`, `pnpm install`,
`pnpm test`. Look for the assertion in stderr and a `Test Files` count below 172.

**Isolated by:** pinning msw back to 2.7.3 with every other bump in that group
left at its new version — 172 files and 960 tests pass, assertion gone. The other
ten packages in the group are not implicated.

**Release when:** a msw release stops aborting the process. Re-check with the
reproduction above; the failure is loud once you know to look for it.

## jsdom — held at 26.1.0 (latest 30.0.1)

**Logged:** 2026-08-07 · **Blocks:** #110 · **Upstream:** jsdom/jsdom#4227 (open)

jsdom 30 returns an empty `NodeList` from a scoped `querySelectorAll` when the
first compound of the selector matches the context element itself. Per DOM the
selector is matched against the whole tree restricted to descendants of the
element, so this must match.

```js
const { JSDOM } = require('jsdom')
const { document } = new JSDOM('<!doctype html><html><body></body></html>').window

const section = document.createElement('section')
section.className = 'root'
section.innerHTML = '<header><button>x</button></header>'
document.body.appendChild(section)

section.querySelectorAll('.root header').length         // 1
section.querySelectorAll('.root header button').length  // 0  <-- should be 1
section.querySelectorAll('header button').length        // 1
```

| jsdom | `.root header` | `.root header button` | `header button` |
|-------|----------------|-----------------------|-----------------|
| 26.1.0 | 1 | **1** | 1 |
| 30.0.1 | 1 | **0** | 1 |

Same result attached or detached. Two compounds resolve correctly; a third
descendant step where the scoping element is the first compound returns nothing.

This surfaces through Vue Test Utils, whose `find`/`findAll` call
`element.querySelectorAll` — in this repo, `WorkflowEditorView.test.ts`.

**Not worked around on purpose.** Rewriting the assertion to `find('header button')`
turns the suite green, and that is the trap: a selector engine that silently
returns an empty result for a valid selector makes every `find(...).exists()`
assertion pass vacuously. The upgrade would trade one visible failure for an
unknown number of silent ones.

**Release when:** jsdom/jsdom#4227 is fixed. Re-check with the snippet above.

## Node — held on the 24 LTS line

**Logged:** 2026-08-14 · **Blocks:** #124 (`frontend/Dockerfile`), #131 (`@types/node`)

Three places name a Node version and they must agree: `NODE_VERSION` in
`.github/workflows/ci.yml`, the `FROM node:` build stage in `frontend/Dockerfile`,
and `@types/node` in `frontend/package.json`. Dependabot proposes them separately,
so each PR on its own silently breaks that agreement.

The Dockerfile bump is the one that matters: the production bundle is built in
that stage, so accepting it alone means the artifact we ship is produced by a
Node major CI never executes. Nothing fails — CI stays green because CI is still
on 24 — which is why this needs a written hold rather than a test.

`@types/node` fails in the opposite direction and just as quietly: types for a
newer major describe APIs the runtime does not have, so `pnpm typecheck` approves
a call that throws at build time.

**Not a bug in Node 26.** The hold is about the version line, not the release:
26 has been Current since 2026-04 and is not LTS until 2026-10, and this project
ships to production, so Active LTS is the floor.

**Release when:** Node 26 reaches LTS. Then bump all three in one commit —
`NODE_VERSION`, the Dockerfile stage, and `@types/node` — never one alone.

## typescript — held at 5.9.3 (latest 7.0.2)

**Logged:** 2026-08-14 · **Blocks:** #146 · **Upstream:** typescript-eslint/typescript-eslint#10940 (open)

typescript-eslint refuses to load against TypeScript 7 — not a rule failure, a
hard refusal at plugin load, so `pnpm lint` exits before evaluating anything:

```
Error: typescript-eslint does not support TS 7.0.
  at @typescript-eslint/eslint-plugin/dist/index.js:50:11
```

That takes every ESLint-backed gate with it, which is all twelve of them. The
upstream issue targets TS >= 7.1, so there is no version pairing that works
today: the ceiling is typescript-eslint's, not ours.

`vue-tsc` was the second blocker — 2.1.10 died with `ERR_PACKAGE_PATH_NOT_EXPORTED`
reaching for TS internals 7 no longer exports — but that half is already cleared
by vue-tsc 3.3.9 (#144). Only the linter is left.

**Reproduction:** set `typescript` to `7.0.2` in `frontend/package.json`,
`pnpm install`, `pnpm lint`. The refusal is immediate and unmistakable.

**Release when:** typescript-eslint ships TS 7 support (their #10940). Do not
reach for the TS 6 side-by-side workaround upstream documents — running the
linter against a different compiler than the build uses is the same
two-sources-of-truth problem the Node hold above describes.

## qdrant-client — held at 1.12.* (latest 1.19.x)

**Logged:** 2026-08-14 · **Blocks:** #140

Not a bug and not a toolchain conflict — an API we still call was removed.
`AsyncQdrantClient.search` is gone in the 1.1x line, replaced by `query_points`,
so widening the pin fails `mypy` with 8 errors across 2 files:

```
contexts/knowledge/infrastructure/qdrant_store.py:167: error:
  "AsyncQdrantClient" has no attribute "search"  [attr-defined]
contexts/knowledge/infrastructure/graphrag_vector_store.py:256: error:
  "AsyncQdrantClient" has no attribute "search"  [attr-defined]
```

Both call sites pass `query_vector` / `query_filter` and read a bare result list;
`query_points` takes `query` and returns a response object whose hits live under
`.points`. The mechanical part is small, but this is the retrieval path for RAG
and GraphRAG, so it needs its own change with tests rather than riding along on a
version bump.

**Reproduction:** widen to `qdrant-client>=1.12,<1.20` in `backend/pyproject.toml`,
`pip install -e '.[dev]'`, `mypy .`.

**Release when:** both call sites move to `query_points`. The ignore rule and this
entry come out in the same commit as the migration.

Note on the ignore: it suppresses update PRs, not Dependabot alerts — a CVE in
qdrant-client still surfaces, it just will not arrive as an automatic bump.
