import type { APIRequestContext, Locator, Page } from '@playwright/test'

import { bearerFor, expect, test } from './fixtures/auth'
import { env } from './fixtures/seed'

// docs/tasks/2026-10-05-guest-sender-marking AC-1, AC-2.
//
// A guest who joins under the room owner's name is badged as a guest for a member
// watching the room -- on the message, in the participant list and in the typing
// line -- while the member's own message carries no badge.
//
// Copy resolved from src/slices/conversation/locales/en.json (guest.*,
// chatroom.guestBadge, chatroom.guestName, chatroom.composerPlaceholder).
const DISPLAY_NAME = /Display Name/
const ENTER_CHATROOM = 'Enter Chatroom'
const COMPOSER_PLACEHOLDER = /^Type a message/
const FALLBACK_OWNER_NAME = 'E2E Ms Lin'

// Shares the seeded room's guest-link flag with 26-guest-session-lifecycle; the
// suite runs on one worker, and each spec turns the flag off when it is done.
test.describe.configure({ mode: 'serial' })

async function setGuestLinks(api: APIRequestContext, roomId: string, on: boolean): Promise<void> {
  const auth = await bearerFor(api)
  const room = await api.get(`/api/chatrooms/${roomId}`, { headers: auth })
  expect(room.ok()).toBe(true)
  const { version } = (await room.json()) as { version: number }
  const patched = await api.patch(`/api/chatrooms/${roomId}`, {
    headers: { ...auth, 'If-Match': String(version) },
    data: { allow_guest_links: on },
  })
  expect(patched.ok(), `PATCH allow_guest_links=${on} -> ${patched.status()}`).toBe(true)
}

async function guestLinkPath(api: APIRequestContext, roomId: string): Promise<string> {
  const resp = await api.get(`/api/chatrooms/${roomId}/guest-link`, { headers: await bearerFor(api) })
  expect(resp.ok()).toBe(true)
  const { chatroom_id: id, guest_token: token } = (await resp.json()) as {
    chatroom_id: string
    guest_token: string
  }
  return `/g/${id}/${token}`
}

// The people rail stands beside the feed only at full desktop width; narrower
// layouts move it into a drawer.
async function wide(page: Page): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 900 })
}

async function liveComposer(page: Page): Promise<Locator> {
  const composer = page.locator('form.composer')
  await expect(composer).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.chat-header__pill--on')).toBeVisible({ timeout: 20_000 })
  return composer
}

async function send(composer: Locator, text: string): Promise<void> {
  await composer.getByRole('textbox', { name: COMPOSER_PLACEHOLDER }).fill(text)
  await composer.getByRole('button', { name: 'Send' }).click()
}

/**
 * The seeded owner's display name (global-setup creates the room as the seed
 * user), setting one when the account has none so a collision is possible.
 * Returns the name and how to put the account back.
 */
async function ownerName(api: APIRequestContext): Promise<{ name: string; restore: () => Promise<void> }> {
  const auth = await bearerFor(api)
  const me = (await (await api.get('/api/auth/me', { headers: auth })).json()) as { display_name: string | null }
  if (me.display_name) return { name: me.display_name, restore: async () => {} }
  const set = await api.patch('/api/auth/me', { headers: auth, data: { display_name: FALLBACK_OWNER_NAME } })
  expect(set.ok(), `PATCH /api/auth/me -> ${set.status()}`).toBe(true)
  return {
    name: FALLBACK_OWNER_NAME,
    restore: async () => {
      await api.patch('/api/auth/me', { headers: await bearerFor(api), data: { display_name: null } })
    },
  }
}

function bubble(page: Page, text: string): Locator {
  return page.getByRole('log').locator('li.bubble-row').filter({ hasText: text })
}

test.describe('Guest sender marking', () => {
  let roomId: string

  test.beforeEach(async ({ request }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    roomId = env('E2E_CHATROOM_ID')!
    await setGuestLinks(request, roomId, true)
  })

  test.afterAll(async ({ request }) => {
    const id = env('E2E_CHATROOM_ID')
    if (id) await setGuestLinks(request, id, false)
  })

  test('the owner sees a guest using their name badged everywhere, and their own message unbadged', async ({
    authedPage: member,
    browser,
    request,
  }) => {
    const link = await guestLinkPath(request, roomId)
    const owner = await ownerName(request)
    const GUEST_NAME = owner.name
    await wide(member)
    await member.goto(`/chatrooms/${roomId}`)
    const memberComposer = await liveComposer(member)

    const ctx = await browser.newContext()
    try {
      const guest = await ctx.newPage()
      await wide(guest)
      await guest.goto(link)
      await guest.getByLabel(DISPLAY_NAME).fill(GUEST_NAME)
      await guest.getByRole('button', { name: ENTER_CHATROOM }).click()
      const guestComposer = await liveComposer(guest)

      const fromGuest = `guest-says-${Date.now()}`
      await send(guestComposer, fromGuest)
      const guestBubble = bubble(member, fromGuest)
      await expect(guestBubble).toBeVisible({ timeout: 15_000 })
      await expect(guestBubble.getByTestId('bubble-guest-badge')).toHaveText('Guest')

      const fromMember = `member-says-${Date.now()}`
      await send(memberComposer, fromMember)
      await expect(bubble(member, fromMember)).toBeVisible({ timeout: 15_000 })
      await expect(bubble(member, fromMember).getByTestId('bubble-guest-badge')).toHaveCount(0)

      // Both rows read the same name; only the guest's carries the badge.
      const rows = member.locator('.chatroom__presence .presence-user').filter({ hasText: GUEST_NAME })
      await expect(rows).toHaveCount(2, { timeout: 15_000 })
      await expect(rows.getByTestId('presence-guest-badge')).toHaveCount(1)
      await expect(rows.filter({ hasText: '(you)' }).getByTestId('presence-guest-badge')).toHaveCount(0)

      // Typing frames expire on the receiving side, so type until the line shows.
      const textbox = guestComposer.getByRole('textbox', { name: COMPOSER_PLACEHOLDER })
      await textbox.pressSequentially('still typing', { delay: 50 })
      await expect(member.locator('.typing')).toContainText(`${GUEST_NAME} (guest)`, { timeout: 10_000 })
    } finally {
      await ctx.close()
      await owner.restore()
    }
  })
})
