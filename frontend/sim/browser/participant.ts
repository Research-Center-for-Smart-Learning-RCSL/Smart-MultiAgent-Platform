import { expect, type Browser, type BrowserContext, type Page } from '@playwright/test'
import { retryAfterSec } from '../lib/time.ts'
import { LOCALE_KEY, sel } from './selectors.ts'

export type FieldValue = string | number | boolean | string[]

export interface ParticipantOptions {
  baseUrl: string
  roomId: string
  name: string
  /** Registered login, or a guest link path with the name to enter. */
  identity: { kind: 'account'; email: string; password: string } | { kind: 'guest'; path: string; displayName: string }
  typingCps: number
  ignoreHttpsErrors: boolean
}

const VIEWPORT = { width: 1440, height: 900 }
const ACTION_TIMEOUT_MS = 20_000

/**
 * One simulated person: an isolated browser context (its own cookie jar, so its own
 * identity) acting through the UI. Login follows `e2e/fixtures/auth.ts:45-83`: wait for
 * hydration, treat the login response as ground truth, honour Retry-After.
 */
export class Participant {
  readonly name: string
  private readonly opts: ParticipantOptions
  private context: BrowserContext | null = null
  private _page: Page | null = null
  /** The id the room uses for this person's messages: user id or guest session id. */
  senderId: string | null = null

  constructor(opts: ParticipantOptions) {
    this.opts = opts
    this.name = opts.name
  }

  get connected(): boolean {
    return this._page !== null
  }

  get page(): Page {
    if (!this._page) throw new Error(`${this.name} is not connected`)
    return this._page
  }

  /** Becomes `connected` only once in the room; a failure closes the half-made context. */
  async connect(browser: Browser, failureShot?: string): Promise<void> {
    if (this._page) return
    const context = await browser.newContext({
      baseURL: this.opts.baseUrl,
      viewport: VIEWPORT,
      ignoreHTTPSErrors: this.opts.ignoreHttpsErrors,
    })
    // Outside the test runner an action has no timeout, so a missing element would hang the step forever.
    context.setDefaultTimeout(ACTION_TIMEOUT_MS)
    try {
      const page = await this.enter(context)
      this.context = context
      this._page = page
    } catch (err) {
      const page = context.pages()[0]
      if (failureShot && page) await page.screenshot({ path: failureShot }).catch(() => undefined)
      await context.close().catch(() => undefined)
      throw err
    }
  }

  private async enter(context: BrowserContext): Promise<Page> {
    await context.addInitScript(
      ([key]) => {
        try {
          localStorage.setItem(key as string, 'en')
        } catch {
          // storage unavailable: the UI falls back to the browser locale
        }
      },
      [LOCALE_KEY],
    )
    const page = await context.newPage()
    if (this.opts.identity.kind === 'account') {
      await this.login(page, this.opts.identity.email, this.opts.identity.password)
      await page.goto(`/chatrooms/${this.opts.roomId}`)
    } else {
      await page.goto(this.opts.identity.path)
      await sel.guestName(page).fill(this.opts.identity.displayName)
      await sel.guestEnter(page).click()
    }
    await expect(page).toHaveURL(new RegExp(`/chatrooms/${this.opts.roomId}$`, 'i'), { timeout: 30_000 })
    await expect(sel.composer(page)).toBeVisible({ timeout: 30_000 })
    if (this.opts.identity.kind === 'guest') this.senderId = await this.readGuestSessionId(page)
    return page
  }

  private async login(page: Page, email: string, password: string): Promise<void> {
    for (let attempt = 1; attempt <= 6; attempt++) {
      await page.goto('/login')
      await sel.loginEmail(page).waitFor({ state: 'visible', timeout: 30_000 })
      await expect(sel.loginSubmit(page)).toBeEnabled({ timeout: 30_000 })
      await sel.loginEmail(page).fill(email)
      await sel.loginPassword(page).fill(password)
      const respP = page.waitForResponse((r) => r.url().includes('/api/auth/login'), { timeout: 15_000 }).catch(() => null)
      await sel.loginSubmit(page).click()
      const resp = await respP
      if (!resp) {
        await page.waitForTimeout(500)
        continue
      }
      // The edge nginx's limit_req on /api/auth/ answers 503 (its default status), not the
      // backend's 429, when several contexts log in within the same second.
      if (resp.status() === 429 || resp.status() === 503) {
        await page.waitForTimeout(retryAfterSec(resp.headers()['retry-after'], 10) * 1000 + 500)
        continue
      }
      if (!resp.ok()) throw new Error(`login for ${this.name} failed with status ${resp.status()}`)
      await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 20_000 })
      return
    }
    throw new Error(`login for ${this.name} did not succeed after 6 attempts`)
  }

  private async readGuestSessionId(page: Page): Promise<string | null> {
    const roomId = this.opts.roomId.toLowerCase()
    return page.evaluate((id) => {
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i)
        if (!key || !key.toLowerCase().includes(id)) continue
        try {
          const v = JSON.parse(localStorage.getItem(key) ?? '') as { guest_session_id?: string }
          if (v.guest_session_id) return v.guest_session_id
        } catch {
          // not a guest hint
        }
      }
      return null
    }, roomId)
  }

  async leave(): Promise<void> {
    const ctx = this.context
    this.context = null
    this._page = null
    if (ctx) await ctx.close()
  }

  /** Shows the presence list, for an end-of-run screenshot. */
  async showPeople(): Promise<void> {
    const tab = sel.peopleTab(this.page)
    if (await tab.isVisible()) await tab.click()
  }

  async screenshot(path: string): Promise<void> {
    if (this._page) await this._page.screenshot({ path, fullPage: false }).catch(() => undefined)
  }

  private charDelayMs(length: number): number {
    // Cap a single message's typing at 20 seconds so a long text does not stall the timeline.
    return Math.min(1000 / this.opts.typingCps, 20_000 / Math.max(length, 1))
  }

  /** How long `type()` takes for a text of `chars` characters. */
  typingDurationMs(chars: number): number {
    return chars * this.charDelayMs(chars)
  }

  async type(text: string): Promise<void> {
    const box = sel.composerText(this.page)
    await box.click()
    await box.pressSequentially(text, { delay: this.charDelayMs(text.length) })
  }

  async sendTyped(snippet: string): Promise<void> {
    const page = this.page
    await expect(sel.send(page)).toBeEnabled({ timeout: 10_000 })
    await sel.send(page).click()
    await expect(sel.ownBubble(page, snippet).last()).toBeVisible({ timeout: 30_000 })
  }

  async say(text: string): Promise<void> {
    await this.type(text)
    await this.sendTyped(bubbleSnippet(text))
  }

  async typePause(text: string, typeChars: number | undefined, pauseSec: number, then: 'send' | 'clear' | 'keep'): Promise<void> {
    const first = typeChars === undefined ? text : text.slice(0, typeChars)
    await this.type(first)
    await this.page.waitForTimeout(pauseSec * 1000)
    if (then === 'clear') return this.clearDraft()
    if (then === 'keep') return
    const rest = text.slice(first.length)
    if (rest) await sel.composerText(this.page).pressSequentially(rest, { delay: this.charDelayMs(rest.length) })
    await this.sendTyped(bubbleSnippet(text))
  }

  async clearDraft(): Promise<void> {
    await sel.composerText(this.page).fill('')
  }

  async openActivity(): Promise<void> {
    const tab = sel.activityTab(this.page)
    await expect(tab).toBeVisible({ timeout: 30_000 })
    await tab.click()
    await expect(sel.activityPanel(this.page)).toBeVisible({ timeout: 10_000 })
  }

  private async ensureActivityOpen(): Promise<void> {
    if (!(await sel.activityPanel(this.page).isVisible())) await this.openActivity()
  }

  private async fillFields(fields: Record<string, FieldValue>): Promise<void> {
    const form = sel.schemaForm(this.page)
    await expect(form).toBeVisible({ timeout: 30_000 })
    for (const [key, value] of Object.entries(fields)) {
      if (Array.isArray(value)) {
        const group = form.locator('.form-field').filter({ has: this.page.locator(`label[for="${key}"]`) })
        for (const option of value) await group.getByLabel(option, { exact: true }).check()
        continue
      }
      const control = form.locator(`[id="${key}"]`)
      await expect(control, `field ${key}`).toBeVisible({ timeout: 10_000 })
      const tag = await control.evaluate((el) => el.tagName.toLowerCase())
      const type = await control.getAttribute('type')
      if (tag === 'select') await control.selectOption(String(value))
      else if (type === 'checkbox') await control.setChecked(value === true)
      else await control.fill(String(value))
    }
  }

  async submit(fields: Record<string, FieldValue>): Promise<void> {
    await this.ensureActivityOpen()
    await this.fillFields(fields)
    await sel.formSubmit(this.page).click()
    await expect(sel.outcome(this.page)).toBeVisible({ timeout: 30_000 })
  }

  async propose(fields: Record<string, FieldValue>, group: string | undefined): Promise<void> {
    await this.ensureActivityOpen()
    // A settled proposal stays on screen until dismissed; the form returns after "Propose another answer".
    const again = sel.proposeAgain(this.page)
    if (await again.isVisible()) {
      await again.click()
      await expect(sel.proposalCard(this.page)).toBeHidden({ timeout: 10_000 })
    }
    if (group) {
      const picker = sel.activityPanel(this.page).locator('select').first()
      if (await picker.isVisible()) await picker.selectOption({ label: group })
    }
    await this.fillFields(fields)
    await sel.formSubmit(this.page).click()
    await expect(sel.proposalCard(this.page)).toBeVisible({ timeout: 30_000 })
  }

  async vote(approve: boolean): Promise<void> {
    await this.ensureActivityOpen()
    const button = approve ? sel.approve(this.page) : sel.reject(this.page)
    await expect(button).toBeEnabled({ timeout: 60_000 })
    await button.click()
    await expect(sel.myVote(this.page)).toBeVisible({ timeout: 15_000 })
  }

  async withdraw(): Promise<void> {
    await this.ensureActivityOpen()
    await sel.withdraw(this.page).click()
  }

  async startActivity(typeName: string): Promise<void> {
    await this.ensureActivityOpen()
    const picker = sel.activityPanel(this.page).locator('select').first()
    await expect(picker).toBeEnabled({ timeout: 30_000 })
    // The picker is enabled before the project's types load, so wait for the option itself.
    const optionValue = (): Promise<string | null> =>
      picker.evaluate((el, name) => {
        const options = Array.from((el as HTMLSelectElement).options)
        return options.find((o) => o.text.includes(name))?.value ?? null
      }, typeName)
    let value: string | null = null
    try {
      await expect.poll(async () => (value = await optionValue()), { timeout: 30_000 }).not.toBeNull()
    } catch {
      const seen = await picker.evaluate((el) => Array.from((el as HTMLSelectElement).options).map((o) => o.text.trim()))
      throw new Error(`no activity named ${typeName} in the facilitator's list (options: ${seen.join(' | ')})`)
    }
    if (!value) throw new Error(`no activity named ${typeName} in the facilitator's list`)
    await picker.selectOption(value)
    await sel.startForRoom(this.page).click()
    await expect(sel.endActivity(this.page)).toBeVisible({ timeout: 30_000 })
  }

  async endActivity(): Promise<void> {
    await this.ensureActivityOpen()
    await sel.endActivity(this.page).click()
    await expect(sel.startForRoom(this.page)).toBeVisible({ timeout: 30_000 })
  }

  async typingVisible(): Promise<boolean> {
    return sel.typingIndicator(this.page).isVisible()
  }
}

/** A short plain-text fragment of a message, for finding its bubble after markdown rendering. */
export function bubbleSnippet(text: string): string {
  const plain = text.replace(/[*_`#>[\]()!~|-]/g, ' ').replace(/\s+/g, ' ').trim()
  return plain.slice(0, 20)
}
