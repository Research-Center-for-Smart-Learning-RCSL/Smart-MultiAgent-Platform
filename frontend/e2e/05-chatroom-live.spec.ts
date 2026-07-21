import type { Locator, Page } from '@playwright/test'

import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// Copy resolved from src/slices/conversation/locales/en.json:
//   composerPlaceholder -> "Type a message…"  (textarea aria-label, ChatroomComposer.vue:36)
//   send                -> "Send"             (icon-only submit aria-label, ChatroomComposer.vue:78)
//   live                -> "live"             (header connection pill, ChatroomHeader.vue:160)
// The message feed is <ol class="messages" role="log"> (ChatroomView.vue:41-48).
const COMPOSER_PLACEHOLDER = /^Type a message/

/**
 * Resolve the composer and wait for the socket to report "live".
 *
 * Sending is REST, but *receiving* is WebSocket fan-out with no replay: a
 * message published before this page's socket has subscribed is never
 * delivered to it. Gating on the pill's live class is the concrete render
 * signal that the subscription is up.
 */
async function openRoom(page: Page, chatroomId: string): Promise<Locator> {
  await page.goto(`/chatrooms/${chatroomId}`)
  const composer = page.locator('form.composer')
  await expect(composer).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.chat-header__pill--on')).toBeVisible({ timeout: 20_000 })
  return composer
}

test.describe('Two-browser chatroom live; edit window; moderator edit', () => {
  test('send and receive a message', async ({ authedPage: page }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!
    const composer = await openRoom(page, chatroomId)

    await composer.getByRole('textbox', { name: COMPOSER_PLACEHOLDER }).fill('Hello E2E')
    await composer.getByRole('button', { name: 'Send' }).click()

    // Scope to the feed: the draft text also lives in the composer until the
    // send resolves, so an unscoped getByText can hit two nodes.
    await expect(page.getByRole('log').getByText('Hello E2E')).toBeVisible({ timeout: 10_000 })
  })

  test('two-browser live sync', async ({ authedPage, adminPage }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!

    // The seeded room belongs to the regular user's project; the admin reaches
    // it through the platform-admin membership bypass (backend access.py:132).
    const senderComposer = await openRoom(authedPage, chatroomId)
    await openRoom(adminPage, chatroomId)

    const msg = `sync-${Date.now()}`
    await senderComposer.getByRole('textbox', { name: COMPOSER_PLACEHOLDER }).fill(msg)
    await senderComposer.getByRole('button', { name: 'Send' }).click()

    await expect(adminPage.getByRole('log').getByText(msg)).toBeVisible({ timeout: 10_000 })
  })
})
