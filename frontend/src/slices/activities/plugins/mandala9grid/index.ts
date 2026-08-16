// The Mandala nine-grid renderer — the platform's first bundled activity plugin
// (R30.17). Bound to the generic key `mandala-9grid` rather than to one course
// unit, so any project can claim the grid for whichever of its Mandala
// activities it wants. The registry is keyed on plugin key globally, and
// `ActivityType.key` is unique only *per scope* — so this renders not just for
// every project naming a type `mandala-9grid`, but for both rows when one
// project holds its own type and an opted-in platform type under that key
// ([R30.02]). That affects presentation only: payload storage and scoring are
// server-side (R30.03), and each row still renders against its own schema
// because this plugin declares no `schema` of its own.

import { createApp } from 'vue'

import { defineActivityPlugin } from '../../sdk/defineActivityPlugin'
import type { ActivityRenderCtx } from '../../sdk/types'
import MandalaGrid from './MandalaGrid.vue'

export const MANDALA_9GRID_KEY = 'mandala-9grid'

export const mandala9GridPlugin = defineActivityPlugin({
  manifest: {
    key: MANDALA_9GRID_KEY,
    version: '1.0.0',
    title: 'Mandala 9-grid',
  },
  render(container: HTMLElement, ctx: ActivityRenderCtx) {
    // A `createApp` root inherits none of the host app's provides. Everything the
    // grid needs is passed as props — `ctx.t` for i18n, `ctx.emit` for the only
    // I/O path the SDK grants.
    const app = createApp(MandalaGrid, {
      schema: ctx.schema,
      t: ctx.t,
      submit: ctx.emit,
    })
    app.mount(container)
    return () => app.unmount()
  },
})
