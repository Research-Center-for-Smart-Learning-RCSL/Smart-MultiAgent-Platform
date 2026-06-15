import { test, expect } from './fixtures/auth'

test.describe('Message edit/delete: author 5-min rule + admin override (M.3)', () => {
  test('send a message then edit it', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)

    const msg = `edit-me-${Date.now()}`
    await page.locator('.composer textarea').fill(msg)
    await page.locator('.composer button[type="submit"]').click()
    await expect(page.locator('.messages').getByText(msg)).toBeVisible({ timeout: 10_000 })

    // Within 5-min window — Edit button should appear.
    const msgEl = page.locator('.msg-actions').last()
    const editBtn = msgEl.getByRole('button', { name: /edit/i })
    await expect(editBtn).toBeVisible({ timeout: 5000 })
    await editBtn.click()

    // The inline edit textarea is inside .md-edit; the composer is inside .composer.
    const editor = page.locator('.md-edit textarea')
    await editor.fill(`${msg}-edited`)
    await page.locator('.md-edit').getByRole('button', { name: /save/i }).click()
    await expect(page.locator('.messages').getByText(`${msg}-edited`)).toBeVisible({ timeout: 5000 })
    await expect(page.locator('.edited').last()).toBeVisible()
  })

  test('delete a message', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)

    const msg = `delete-me-${Date.now()}`
    await page.locator('.composer textarea').fill(msg)
    await page.locator('.composer button[type="submit"]').click()
    await expect(page.locator('.messages').getByText(msg)).toBeVisible({ timeout: 10_000 })

    const msgEl = page.locator('.msg-actions').last()
    const deleteBtn = msgEl.getByRole('button', { name: /delete/i })
    await expect(deleteBtn).toBeVisible({ timeout: 5000 })
    await deleteBtn.click()

    // ElMessageBox.confirm renders OK/Cancel; wait for the dialog to appear.
    const dialog = page.locator('.el-message-box')
    await expect(dialog).toBeVisible({ timeout: 3000 })
    await dialog.getByRole('button', { name: /ok|confirm/i }).click()
    await expect(page.locator('.messages').getByText(msg)).not.toBeVisible({ timeout: 5000 })
  })

  test('admin can edit another user message', async ({ authedPage, adminPage }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await authedPage.goto(`/chatrooms/${chatroomId}`)

    const msg = `admin-edit-${Date.now()}`
    await authedPage.locator('.composer textarea').fill(msg)
    await authedPage.locator('.composer button[type="submit"]').click()
    await expect(authedPage.locator('.messages').getByText(msg)).toBeVisible({ timeout: 10_000 })

    // Admin should see Edit on the user's message.
    await adminPage.goto(`/chatrooms/${chatroomId}`)
    await expect(adminPage.locator('.messages').getByText(msg)).toBeVisible({ timeout: 10_000 })
    const msgActions = adminPage.locator('.msg-actions').last()
    await expect(msgActions.getByRole('button', { name: /edit/i })).toBeVisible({ timeout: 5000 })
  })
})
