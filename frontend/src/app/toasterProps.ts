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
    duration: 4_000,
    containerAriaLabel: t('app.notifications.label'),
    // vue-sonner renders the close button only when this is true; the label
    // below is inert without it.
    closeButton: true,
    toastOptions: { closeButtonAriaLabel: t('app.notifications.close') },
  }
}
