// Host<->plugin bridge (AC-7). The host always talks to a plugin through a
// `HostBridge`; v1 uses `InProcessBridge` (trusted, same-realm). The
// `IframeBridge` (FU-1) is a typed stub carrying the postMessage contract so the
// enforcing sandbox for untrusted plugins is a bridge swap, not a rearchitecture.

import type {
  ActivityPlugin,
  ActivityRenderCtx,
  ActivitySessionRef,
  ActivitySubmissionResult,
  ActivityTeardown,
  ActivityTranslate,
  HostToPluginMessage,
  JSONSchema,
  PluginToHostMessage,
} from './types'

export interface BridgeMountOptions {
  container: HTMLElement
  schema: JSONSchema
  session: ActivitySessionRef
  t: ActivityTranslate
  /** Host-mediated backend submit — the only path a plugin has to the server. */
  submit(payload: unknown): Promise<ActivitySubmissionResult>
  /** Host-mediated draft report ([R32.01]). Optional so a caller with no socket
   *  — a test, or any future host outside a chatroom — mounts a plugin whose
   *  `ctx.draft` is a working no-op rather than a crash. Reporting nothing is
   *  always the safe direction here. */
  reportDraft?: (payload: unknown) => void
}

export interface HostBridge {
  /** Mount `plugin` against a host-built ctx. Return its teardown. */
  mount(plugin: ActivityPlugin, options: BridgeMountOptions): ActivityTeardown
}

/**
 * v1 bridge: the plugin runs in the host's JS realm and `ctx.emit` calls the
 * host submit directly. Non-reachability of session/network is a *contract*
 * here (first-party trust), not an enforced boundary — that enforcement is the
 * deferred {@link IframeBridge}.
 */
export class InProcessBridge implements HostBridge {
  mount(plugin: ActivityPlugin, options: BridgeMountOptions): ActivityTeardown {
    // Exactly the five contract members — no other enumerable keys (AC-3).
    // The count moved from four to five with §32; `sdk.test.ts` asserts the exact
    // set, so this comment and that test have to change together or the comment
    // becomes the lie the test is protecting against.
    const ctx: ActivityRenderCtx = {
      schema: options.schema,
      session: options.session,
      emit: (payload) => options.submit(payload),
      // A statement body, not an expression one: `(p) => options.reportDraft?.(p)`
      // returns whatever the host's callback happened to return, and a plugin that
      // can read that has a signal about whether anyone is listening. `void` in
      // the type does not stop a value at runtime, so the discard is explicit.
      draft: (payload) => {
        options.reportDraft?.(payload)
      },
      t: options.t,
    }
    const teardown = plugin.render(options.container, ctx)
    return typeof teardown === 'function' ? teardown : () => {}
  }
}

/**
 * Deferred (FU-1). Fixes the postMessage message-kind contract for an isolating
 * `sandbox="allow-scripts"` iframe with no same-origin access. Not wired in v1;
 * `mount` throws so an accidental selection fails loudly rather than silently
 * running an untrusted plugin in-process.
 */
export class IframeBridge implements HostBridge {
  mount(_plugin: ActivityPlugin, _options: BridgeMountOptions): ActivityTeardown {
    throw new Error(
      'IframeBridge is deferred (FU-1): v1 mounts first-party plugins in-process',
    )
  }

  /** host -> plugin frame (schema handshake, submit result). */
  protected postToPlugin(_message: HostToPluginMessage): void {
    throw new Error('IframeBridge is deferred (FU-1)')
  }

  /** plugin frame -> host (emit). */
  protected onPluginMessage(_message: PluginToHostMessage): void {
    throw new Error('IframeBridge is deferred (FU-1)')
  }
}
