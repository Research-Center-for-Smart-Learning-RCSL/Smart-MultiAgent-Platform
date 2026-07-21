import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// Rendered markup (src/slices/conversation/components/ChatroomComposer.vue):
//   form.composer > textarea.composer__textarea      (aria-label "Type a message…")
//   ul.composer__uploads > li.upload > .upload__name (pending-attachment chips)
// Feed (ChatroomView.vue:41): ol.messages, message bubbles are li.bubble-row.
const COMPOSER_PLACEHOLDER = /^Type a message/

test.describe('tus file upload with progress tracking', () => {
  test('upload a file via drag-and-drop', async ({ authedPage: page }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!

    // useChatroomAttachments resolves the project id reactively through
    // room -> workspace and bails out (no chip at all) if the drop lands before
    // that chain settles. Arm the waiter before navigating so the response
    // cannot be missed.
    const workspaceLoaded = page.waitForResponse(
      (r) => /\/api\/workspaces\/[^/]+$/.test(new URL(r.url()).pathname),
      { timeout: 20_000 },
    )
    await page.goto(`/chatrooms/${chatroomId}`)

    const composer = page.locator('form.composer')
    await expect(composer).toBeVisible({ timeout: 10_000 })
    await workspaceLoaded

    // Create a synthetic file and drop it onto the composer textarea.
    const textarea = composer.locator('textarea')
    const dataTransfer = await page.evaluateHandle(() => {
      const dt = new DataTransfer()
      const content = 'E2E test file content — ' + Date.now()
      const file = new File([content], 'e2e-upload.txt', { type: 'text/plain' })
      dt.items.add(file)
      return dt
    })

    await textarea.dispatchEvent('drop', { dataTransfer })
    await dataTransfer.dispose()

    // The pending-upload chip renders immediately (status "uploading") and
    // carries the filename regardless of how the transfer resolves.
    const attachments = composer.locator('ul.composer__uploads li.upload')
    await expect(attachments.first()).toBeVisible({ timeout: 10_000 })
    await expect(attachments.first()).toContainText('e2e-upload.txt')
  })

  test('send a text message', async ({ authedPage: page }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!
    await page.goto(`/chatrooms/${chatroomId}`)

    const composer = page.locator('form.composer')
    await expect(composer).toBeVisible({ timeout: 10_000 })

    await composer.getByRole('textbox', { name: COMPOSER_PLACEHOLDER }).fill('E2E test message')
    await composer.getByRole('button', { name: 'Send' }).click()

    // Message should appear in the feed (optimistic bubble first, then the
    // persisted twin at the same position).
    await expect(page.locator('.messages li.bubble-row').last()).toContainText(
      'E2E test message',
      { timeout: 10_000 },
    )
  })
})
