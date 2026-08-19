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
    await expect(toaster).toHaveAttribute('data-y-position', 'top')
    await expect(toaster).toHaveAttribute('data-x-position', 'right')
    const toastBox = page.locator('[data-sonner-toast]').filter({ hasText: 'Profile updated.' })
    const geometry = await toastBox.evaluate((element) => {
      const style = getComputedStyle(element)
      const toasterStyle = getComputedStyle(element.closest('[data-sonner-toaster]')!)
      const rect = element.getBoundingClientRect()
      return {
        position: toasterStyle.position,
        zIndex: toasterStyle.zIndex,
        toastPosition: style.position,
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
    expect(geometry.toastPosition).toBe('absolute')
    expect(geometry.top).toBeGreaterThanOrEqual(0)
    expect(geometry.left).toBeGreaterThanOrEqual(0)
    expect(geometry.right).toBeLessThanOrEqual(geometry.viewportWidth)
    expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewportHeight)
  })

  test('toast status colors resolve from the light and dark theme tokens', async ({ page }) => {
    await page.goto('/login')
    await page.locator('body').evaluate((body) => {
      body.innerHTML = `
        <ol data-sonner-toaster>
          <li data-sonner-toast data-styled="true" data-type="success">success</li>
          <li data-sonner-toast data-styled="true" data-type="error">error</li>
          <li data-sonner-toast data-styled="true" data-type="warning">warning</li>
          <li data-sonner-toast data-styled="true" data-type="info">info</li>
        </ol>
      `
    })

    const tokenNames = {
      success: ['--color-success-tint', '--color-success-on'],
      error: ['--color-danger-tint', '--color-danger-on'],
      warning: ['--color-warning-tint', '--color-warning-on'],
      info: ['--color-info-tint', '--color-info-on'],
    } as const

    for (const theme of ['light', 'dark'] as const) {
      await page.locator('html').evaluate((root, value) => {
        if (value === 'dark') root.dataset.theme = value
        else delete root.dataset.theme
      }, theme)

      for (const [type, [backgroundToken, foregroundToken]] of Object.entries(tokenNames)) {
        const colors = await page
          .locator(`[data-sonner-toast][data-type="${type}"]`)
          .evaluate((toast, tokens) => {
            const reference = document.createElement('div')
            reference.style.backgroundColor = `var(${tokens.backgroundToken})`
            reference.style.color = `var(${tokens.foregroundToken})`
            reference.style.border = `1px solid color-mix(in srgb, var(${tokens.foregroundToken}) 25%, transparent)`
            document.body.append(reference)

            const actual = getComputedStyle(toast)
            const expected = getComputedStyle(reference)
            const result = {
              actual: [actual.backgroundColor, actual.color, actual.borderColor],
              expected: [expected.backgroundColor, expected.color, expected.borderColor],
            }
            reference.remove()
            return result
          }, { backgroundToken, foregroundToken })

        expect(colors.actual, `${theme} ${type}`).toEqual(colors.expected)
      }
    }
  })
})
