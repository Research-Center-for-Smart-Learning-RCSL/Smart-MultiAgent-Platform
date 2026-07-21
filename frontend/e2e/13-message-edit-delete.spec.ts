import type { Page } from '@playwright/test'
import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// Locator notes for this file (ChatroomMessageBubble.vue):
//   * a message renders as <li class="bubble-row" id="msg-{id}">
//   * the edit/delete/copy controls live in .bubble__actions, revealed on
//     :hover / :focus-within of the row
//   * they are also gated on canEdit/canDelete, which both return false while
//     the send is still optimistic (.bubble-row--pending)
//   * the inline editor is .bubble__edit, not a separate composer
async function sendMessage(page: Page, text: string) {
  await page.locator('.composer textarea').fill(text)
  await page.locator('.composer').getByRole('button', { name: 'Send' }).click()
}

// Resolves the sent message to a stable id-based locator. The optimistic bubble
// carries a client-side id that is swapped for the persisted one on ack, so the
// id is only worth reading once the pending class is gone. Filtering by text
// also stops working the moment the bubble enters edit mode (the body becomes a
// <textarea>, whose value is not text content), hence the id handle.
async function locateMessage(page: Page, text: string) {
  const sent = page.locator('.bubble-row').filter({ hasText: text })
  await expect(sent).toBeVisible({ timeout: 10_000 })
  await expect(sent).not.toHaveClass(/bubble-row--pending/, { timeout: 10_000 })
  const id = await sent.getAttribute('id')
  // The id is a UUID, which is not a valid bare CSS id selector.
  return page.locator(`[id="${id}"]`)
}

test.describe('Message edit/delete: author 5-min rule + admin override (M.3)', () => {
  test('send a message then edit it', async ({ authedPage: page }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!
    await page.goto(`/chatrooms/${chatroomId}`)

    const msg = `edit-me-${Date.now()}`
    await sendMessage(page, msg)
    const bubble = await locateMessage(page, msg)

    // Within 5-min window — Edit appears in the hover toolbar.
    await bubble.hover()
    const editBtn = bubble.getByRole('button', { name: 'Edit', exact: true })
    await expect(editBtn).toBeVisible({ timeout: 5000 })
    await editBtn.click()

    // The inline editor is labelled by the same "Edit" string; it is a textbox,
    // so it never collides with the button above.
    const editor = bubble.getByRole('textbox', { name: 'Edit' })
    await editor.fill(`${msg}-edited`)
    await bubble.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(bubble.locator('.bubble__body')).toContainText(`${msg}-edited`, { timeout: 5000 })
    await expect(bubble.locator('.bubble__edited')).toBeVisible()
  })

  test('delete a message', async ({ authedPage: page }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!
    await page.goto(`/chatrooms/${chatroomId}`)

    const msg = `delete-me-${Date.now()}`
    await sendMessage(page, msg)
    const bubble = await locateMessage(page, msg)

    await bubble.hover()
    const deleteBtn = bubble.getByRole('button', { name: 'Delete', exact: true })
    await expect(deleteBtn).toBeVisible({ timeout: 5000 })
    await deleteBtn.click()

    // SConfirmDialog renders SModal with role="alertdialog" (not "dialog") and
    // falls back to app.confirm / app.cancel for its button labels.
    const dialog = page.getByRole('alertdialog')
    await expect(dialog).toBeVisible({ timeout: 3000 })
    await dialog.getByRole('button', { name: 'Confirm', exact: true }).click()
    await expect(bubble).toHaveCount(0, { timeout: 5000 })
  })

  test('admin can edit another user message', async ({ authedPage, adminPage }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!
    await authedPage.goto(`/chatrooms/${chatroomId}`)

    const msg = `admin-edit-${Date.now()}`
    await sendMessage(authedPage, msg)
    await locateMessage(authedPage, msg)

    // Admin should see Edit on the user's message (canEdit short-circuits on
    // is_admin, so the 5-minute window does not apply).
    await adminPage.goto(`/chatrooms/${chatroomId}`)
    const adminBubble = adminPage.locator('.bubble-row').filter({ hasText: msg })
    await expect(adminBubble).toBeVisible({ timeout: 10_000 })
    await adminBubble.hover()
    await expect(
      adminBubble.getByRole('button', { name: 'Edit', exact: true }),
    ).toBeVisible({ timeout: 5000 })
  })
})
