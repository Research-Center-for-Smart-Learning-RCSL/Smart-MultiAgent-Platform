import { http, HttpResponse } from 'msw'

import { i18n } from '@shared/i18n'

import { server } from '../../../../tests/mocks/server'
import en from '../locales/en.json'

// The i18n singleton boots empty in tests (slices register lazy loaders at install
// time, which tests do not run). Merge the skills bundle plus the shared app keys the
// atoms reference so t() resolves real strings.
export function installMessages(): void {
  i18n.global.mergeLocaleMessage('en', en as Record<string, unknown>)
  i18n.global.mergeLocaleMessage('en', {
    app: { save: 'Save', cancel: 'Cancel', confirm: 'Confirm' },
  })
}

const EMPTY_PAGE = { items: [], total: 0 }

/** Seed the list endpoint a workbench fires for a scope path (e.g. `/projects/p_1`). */
export function seedScopeList(scopePath: string, items: unknown[] = []): void {
  server.use(http.get(`/api${scopePath}/skills`, () => HttpResponse.json({ items, total: items.length })))
}

export function seedEmptyList(scopePath: string): void {
  server.use(http.get(`/api${scopePath}/skills`, () => HttpResponse.json(EMPTY_PAGE)))
}

export async function settle(ms = 60): Promise<void> {
  await new Promise((r) => setTimeout(r, ms))
}
