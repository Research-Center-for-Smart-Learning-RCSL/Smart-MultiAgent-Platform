import type { Locator, Page } from '@playwright/test'

/*
 * Every selector the simulator depends on, in one place (spec §6.5). The chat surfaces
 * carry few testids, so most of these are accessible names from the `en` catalogue
 * (each context pins `smap-locale` to `en`) or component classes. FU-2 replaces the
 * class selectors with testids.
 */

export const LOCALE_KEY = 'smap-locale'

export const sel = {
  loginEmail: (p: Page): Locator => p.getByLabel(/email/i),
  loginPassword: (p: Page): Locator => p.getByLabel(/password/i),
  loginSubmit: (p: Page): Locator => p.getByRole('button', { name: /log\s*in|sign\s*in/i }),
  guestName: (p: Page): Locator => p.getByLabel(/Display Name/),
  guestEnter: (p: Page): Locator => p.getByRole('button', { name: 'Enter Chatroom' }),
  composer: (p: Page): Locator => p.locator('form.composer'),
  composerText: (p: Page): Locator => p.getByRole('textbox', { name: /^Type a message/ }),
  send: (p: Page): Locator => p.locator('form.composer').getByRole('button', { name: 'Send', exact: true }),
  ownBubble: (p: Page, snippet: string): Locator =>
    p.getByRole('log').locator('li.bubble-row.bubble-row--own:not(.bubble-row--pending)').filter({ hasText: snippet }),
  typingIndicator: (p: Page): Locator => p.locator('p.typing.typing--visible'),
  activityTab: (p: Page): Locator => p.getByRole('tab', { name: 'Activity' }),
  peopleTab: (p: Page): Locator => p.getByRole('tab', { name: 'People' }),
  activityPanel: (p: Page): Locator => p.locator('section.activity-panel'),
  schemaForm: (p: Page): Locator => p.locator('section.activity-panel form.schema-form'),
  formSubmit: (p: Page): Locator => p.locator('section.activity-panel form.schema-form button[type="submit"]'),
  outcome: (p: Page): Locator => p.locator('.activity-host__outcome'),
  proposalCard: (p: Page): Locator => p.getByTestId('group-proposal-card'),
  approve: (p: Page): Locator => p.getByTestId('group-proposal-approve'),
  reject: (p: Page): Locator => p.getByTestId('group-proposal-reject'),
  myVote: (p: Page): Locator => p.getByTestId('group-proposal-my-vote'),
  withdraw: (p: Page): Locator => p.getByTestId('group-proposal-withdraw'),
  proposeAgain: (p: Page): Locator => p.getByTestId('group-proposal-retry'),
  startForRoom: (p: Page): Locator => p.getByRole('button', { name: 'Start for room' }),
  endActivity: (p: Page): Locator => p.getByRole('button', { name: 'End activity' }),
}
