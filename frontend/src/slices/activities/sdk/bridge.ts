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
  /** Host-mediated backend submit — the ONLY I/O path a plugin is given. */
  submit(payload: unknown): Promise<ActivitySubmissionResult>
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
    // Exactly the four contract members — no other enumerable keys (AC-3).
    const ctx: ActivityRenderCtx = {
      schema: options.schema,
      session: options.session,
      emit: (payload) => options.submit(payload),
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
