import { test, expect } from './fixtures/auth'

test.describe('Transient feedback channels', () => {
  test('profile success toast is a fixed token-layer overlay inside the viewport', async ({
    authedPage: page,
  }) => {
    await page.goto('/profile')
    await expect(page.getByRole('heading', { name: 'Profile' })).toBeVisible()

    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText('Profile updated.')).toBeVisible()

    const toaster = page.locator('[data-sonner-toaster]')
    const geometry = await toaster.evaluate((element) => {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return {
        position: style.position,
        zIndex: style.zIndex,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        left: rect.left,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      }
    })

    expect(geometry.position).toBe('fixed')
    expect(geometry.zIndex).toBe('500')
    expect(geometry.top).toBeGreaterThanOrEqual(0)
    expect(geometry.left).toBeGreaterThanOrEqual(0)
    expect(geometry.right).toBeLessThanOrEqual(geometry.viewportWidth)
    expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewportHeight)
  })
})
