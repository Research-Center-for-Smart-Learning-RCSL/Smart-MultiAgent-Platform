# Dependency Holds

Dependencies deliberately kept below their latest version, with the evidence and
the condition for releasing the hold.

Dependabot re-proposes these weekly. Without this file the investigation gets
re-derived from scratch every time, or the bump lands and breaks CI again. Each
entry must carry a reproduction, so the next person can re-check in minutes
rather than re-bisect.

Delete an entry when its hold is released — a stale entry is worse than none.

## msw — held at 2.7.3 (latest 2.15.0)

**Logged:** 2026-08-07 · **Blocks:** the frontend-minor-and-patch group (#108)

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
