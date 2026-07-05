import { http, HttpResponse } from 'msw'

import { i18n } from '@shared/i18n'

import { server } from '../../../../tests/mocks/server'
import en from '../locales/en.json'

// The i18n singleton boots with empty message bundles (slices register lazy
// loaders at app install time, which tests do not run). Merge the prompt-studio
// bundle plus the handful of shared app keys the forms reference so t() resolves
// real strings instead of raw keys.
export function installMessages(): void {
  i18n.global.mergeLocaleMessage('en', en as Record<string, unknown>)
  i18n.global.mergeLocaleMessage('en', {
    app: { save: 'Save', cancel: 'Cancel', confirm: 'Confirm' },
  })
}

const EMPTY_ENVELOPE = { configured: false, config: null }

/** Seed the four endpoints PromptStudioSettings fires, for any config scope. */
export function seedConfigScope(configPath: string, templatePath: string): void {
  server.use(
    http.get(`/api${configPath}/config`, () => HttpResponse.json(EMPTY_ENVELOPE)),
    http.get(`/api${templatePath}`, () => HttpResponse.json([])),
    http.get('/api/keys', () => HttpResponse.json([])),
    http.get('/api/model-catalog', () => HttpResponse.json({ chat: [] })),
  )
}

export async function settle(ms = 60): Promise<void> {
  await new Promise((r) => setTimeout(r, ms))
}
