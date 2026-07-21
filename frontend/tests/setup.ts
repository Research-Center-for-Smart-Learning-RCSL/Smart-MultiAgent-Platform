import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './mocks/server'

// The `/api/*` catch-all in handlers.ts already reports and absorbs unmocked
// calls, so nothing reaches this. Left as 'warn' deliberately: msw 2.7's
// 'error' strategy rejects with an InternalError that escapes as an unhandled
// rejection and fails the run outright.
beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// jsdom implements no layout engine, so CodeMirror's rAF-scheduled remeasure
// crashes reaching for Range#getClientRects (OQ-1 in the
// 2026-07-16-code-editor-syntax-highlighting task spec). Global and guarded
// so it's a no-op for every test that never touches CodeMirror.
if (!Range.prototype.getClientRects) {
  Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} }) as unknown as DOMRectList
}
if (!Range.prototype.getBoundingClientRect) {
  Range.prototype.getBoundingClientRect = () => ({
    x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}),
  }) as DOMRect
}
