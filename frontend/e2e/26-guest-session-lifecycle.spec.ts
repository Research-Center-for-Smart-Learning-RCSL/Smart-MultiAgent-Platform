import type { APIRequestContext, Page } from '@playwright/test'

import { bearerFor, expect, test } from './fixtures/auth'
import { env } from './fixtures/seed'

// docs/tasks/2026-10-05-guest-frontend-session-lifecycle §8 item 7.
//
// An anonymous guest's session survives a reload and a second tab (the boot
// restore from the room's guest cookie), ends visibly when the owner turns guest
// links off, and a signed-in member who enters as a guest leaves the account
// shell behind until a reload brings the account back.
//
// Copy resolved from src/slices/conversation/locales/en.json (guest.*,
// chatroom.settingsLabel). The sidebar's navigation is not a usable account
// signal here: a chatroom route collapses the sidebar and makes it inert.
const DISPLAY_NAME = /Display Name/
const ENTER_CHATROOM = 'Enter Chatroom'
const ENTER_AS_GUEST = 'Enter as Guest'
const DISABLED_BANNER = 'Guest access has been disabled by the room owner.'
const ROOM_SETTINGS = 'Settings'

// The tests share the seeded room and flip its guest links, so they cannot
// interleave with each other.
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

async function expectInRoom(page: Page, roomId: string): Promise<void> {
  await expect(page).toHaveURL(new RegExp(`/chatrooms/${roomId}$`), { timeout: 20_000 })
  await expect(page.locator('form.composer')).toBeVisible({ timeout: 20_000 })
}

async function enterWithName(page: Page, name: string): Promise<void> {
  await page.getByLabel(DISPLAY_NAME).fill(name)
  await page.getByRole('button', { name: ENTER_CHATROOM }).click()
}

test.describe('Guest session lifecycle', () => {
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

  test('a guest stays in the room across a reload and a second tab, then sees links turned off', async ({
    browser,
    request,
  }) => {
    const link = await guestLinkPath(request, roomId)
    const ctx = await browser.newContext()
    try {
      const page = await ctx.newPage()
      await page.goto(link)
      await enterWithName(page, 'E2E Guest Alice')
      await expectInRoom(page, roomId)

      await page.reload()
      await expectInRoom(page, roomId)
      await expect(page.getByText('E2E Guest Alice').first()).toBeVisible({ timeout: 10_000 })

      const second = await ctx.newPage()
      await second.goto(`/c/${roomId}`)
      await expectInRoom(second, roomId)

      // A reload is the deterministic way to exercise the refresh path here;
      // a live socket only learns of the change when its next ticket or
      // refresh is refused.
      await setGuestLinks(request, roomId, false)
      await second.reload()
      await expect(second.getByText(DISABLED_BANNER)).toBeVisible({ timeout: 20_000 })
      await expect(second).toHaveURL(new RegExp(`/chatrooms/${roomId}$`))
    } finally {
      await ctx.close()
    }
  })

  test('a signed-in member confirms entering as a guest and leaves the account shell', async ({
    authedPage: page,
    request,
  }) => {
    const link = await guestLinkPath(request, roomId)
    const roomSettings = page.locator('.chat-header').getByRole('button', { name: ROOM_SETTINGS, exact: true })

    // Baseline: the member's own room header offers Settings, so its absence
    // below means the account is gone rather than that the control never shows.
    await page.goto(`/chatrooms/${roomId}`)
    await expectInRoom(page, roomId)
    await expect(roomSettings).toBeVisible({ timeout: 20_000 })

    await page.goto(link)
    await page.getByRole('button', { name: ENTER_AS_GUEST }).click()
    const dialog = page.getByRole('alertdialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: ENTER_AS_GUEST }).click()

    await enterWithName(page, 'E2E Teacher As Guest')
    await expectInRoom(page, roomId)
    await expect(roomSettings).toHaveCount(0)

    // The account's refresh cookie survived the local clear (Q-3), and boot
    // tries the account first (Q-1).
    await page.reload()
    await expectInRoom(page, roomId)
    await expect(roomSettings).toBeVisible({ timeout: 20_000 })
  })
})
