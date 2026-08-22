/** Safe-area-guarded replacements for vue-sonner's own viewport offsets.
 *
 *  `index.html` opts into `viewport-fit=cover`, so the library's plain `24px` /
 *  `16px` are measured from a layout viewport edge that sits behind the status
 *  bar or display cutout. `max()` and not `calc()`: these are gutters whose
 *  only job is to keep clear of the edge, unlike the impersonation banner's
 *  interior padding.
 *
 *  The floors are `VIEWPORT_OFFSET` and `MOBILE_VIEWPORT_OFFSET`
 *  (`vue-sonner/lib/index.js`) spelled out, which is what makes this a no-op on
 *  a device reporting no inset. A vue-sonner upgrade that changes either
 *  default drifts from these silently.
 *
 *  All four edges on both, because `position` is a prop: insetting only the two
 *  edges today's value happens to use would leave a surface that protects half
 *  of itself, one prop change later. vue-sonner applies each edge only for the
 *  positions that use it, so the unused ones cost nothing.
 *
 *  Written as literals rather than generated per edge: the guard that keeps
 *  this file honest is a source scan (`mobileViewportContract.test.ts`), and it
 *  can only see the spelling that is actually in the file.
 */
const OFFSET = {
  top: 'max(24px, env(safe-area-inset-top, 0px))',
  right: 'max(24px, env(safe-area-inset-right, 0px))',
  bottom: 'max(24px, env(safe-area-inset-bottom, 0px))',
  left: 'max(24px, env(safe-area-inset-left, 0px))',
} as const

const MOBILE_OFFSET = {
  top: 'max(16px, env(safe-area-inset-top, 0px))',
  right: 'max(16px, env(safe-area-inset-right, 0px))',
  bottom: 'max(16px, env(safe-area-inset-bottom, 0px))',
  left: 'max(16px, env(safe-area-inset-left, 0px))',
} as const

/** The single Toaster configuration the app mounts.
 *
 *  Lives outside App.vue so `ToasterAccessibility.test.ts` can mount the props
 *  the app actually ships. When the test supplied its own, an a11y prop could
 *  be dropped from App.vue without any spec failing — which is how the
 *  localized close-button label shipped with no close button to attach to.
 */
export function toasterProps(t: (key: string) => string) {
  return {
    position: 'top-right' as const,
    offset: OFFSET,
    mobileOffset: MOBILE_OFFSET,
    duration: 4_000,
    containerAriaLabel: t('app.notifications.label'),
    // vue-sonner renders the close button only when this is true; the label
    // below is inert without it.
    closeButton: true,
    toastOptions: { closeButtonAriaLabel: t('app.notifications.close') },
  }
}
