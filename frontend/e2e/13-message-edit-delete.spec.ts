import { test, expect } from './fixtures/auth'

test.describe('Message edit/delete: author 5-min rule + admin override (M.3)', () => {
  test('send a message then edit it', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)

    const msg = `edit-me-${Date.now()}`
    const composer = page.getByRole('textbox').last()
    await composer.fill(msg)
    await composer.press('Enter')
    await expect(page.getByText(msg)).toBeVisible({ timeout: 10_000 })

    // Within 5-min window — Edit button should appear on hover/focus.
    const msgEl = page.locator('.msg-actions').last()
    const editBtn = msgEl.getByRole('button', { name: /edit/i })
    await expect(editBtn).toBeVisible({ timeout: 5000 })
    await editBtn.click()

    const editor = page.locator('textarea[aria-label]').last()
    await editor.fill(`${msg}-edited`)
    await page.getByRole('button', { name: /save/i }).last().click()
    await expect(page.getByText(`${msg}-edited`)).toBeVisible({ timeout: 5000 })
    await expect(page.locator('.edited').last()).toBeVisible()
  })

  test('delete a message', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)

    const msg = `delete-me-${Date.now()}`
    const composer = page.getByRole('textbox').last()
    await composer.fill(msg)
    await composer.press('Enter')
    await expect(page.getByText(msg)).toBeVisible({ timeout: 10_000 })

    const msgEl = page.locator('.msg-actions').last()
    const deleteBtn = msgEl.getByRole('button', { name: /delete/i })
    await expect(deleteBtn).toBeVisible({ timeout: 5000 })
    await deleteBtn.click()

    // Confirm deletion dialog.
    const confirmBtn = page.locator('.el-message-box')
      .getByRole('button', { name: /confirm|ok|yes|delete/i })
    if (await confirmBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await confirmBtn.click()
    }
    await expect(page.getByText(msg)).not.toBeVisible({ timeout: 5000 })
  })

  test('admin can edit another user message', async ({ authedPage, adminPage }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await authedPage.goto(`/chatrooms/${chatroomId}`)

    const msg = `admin-edit-${Date.now()}`
    const composer = authedPage.getByRole('textbox').last()
    await composer.fill(msg)
    await composer.press('Enter')
    await expect(authedPage.getByText(msg)).toBeVisible({ timeout: 10_000 })

    // Admin should see Edit on the user's message.
    await adminPage.goto(`/chatrooms/${chatroomId}`)
    await expect(adminPage.getByText(msg)).toBeVisible({ timeout: 10_000 })
    const msgActions = adminPage.locator('.msg-actions').last()
    await expect(msgActions.getByRole('button', { name: /edit/i })).toBeVisible({ timeout: 5000 })
  })
})
