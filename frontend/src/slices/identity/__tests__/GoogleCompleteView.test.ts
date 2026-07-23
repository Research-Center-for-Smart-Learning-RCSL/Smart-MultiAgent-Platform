import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import GoogleCompleteView from '../views/GoogleCompleteView.vue'

describe('GoogleCompleteView', () => {
  it('renders the signing-in state while hydrating', async () => {
    // The backend already set the refresh cookie and 302'd here; the view hydrates
    // the session from that cookie, then redirects. At mount it shows the spinner.
    const wrapper = await renderView(GoogleCompleteView)
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.text()).toContain('identity.googleComplete.signingIn')
  })
})
