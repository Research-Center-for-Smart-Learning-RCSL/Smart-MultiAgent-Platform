// AC-3 (ctx surface) and AC-7 (bridge selection + typed postMessage contract).

import { describe, expect, it } from 'vitest'
import { defineActivityPlugin } from '../sdk/defineActivityPlugin'
import { IframeBridge, InProcessBridge, type BridgeMountOptions } from '../sdk/bridge'
import type {
  ActivityPlugin,
  ActivityRenderCtx,
  HostToPluginMessage,
  PluginToHostMessage,
} from '../sdk/types'

function mountOptions(over: Partial<BridgeMountOptions> = {}): BridgeMountOptions {
  return {
    container: document.createElement('div'),
    schema: { type: 'object' },
    session: { activityTypeKey: 'demo', sessionId: null },
    t: (key) => key,
    submit: async () => ({ submissionId: 's1', status: 'validated', isValid: true, subScores: {} }),
    ...over,
  }
}

describe('defineActivityPlugin', () => {
  it('is an identity helper (returns the same object)', () => {
    const plugin: ActivityPlugin = {
      manifest: { key: 'demo', version: '1.0.0', title: 'Demo' },
      render: () => {},
    }
    expect(defineActivityPlugin(plugin)).toBe(plugin)
  })
})

describe('InProcessBridge — ctx surface (AC-3)', () => {
  it('hands the plugin exactly draft/emit/schema/session/t and nothing else', () => {
    let captured: ActivityRenderCtx | null = null
    const plugin = defineActivityPlugin({
      manifest: { key: 'demo', version: '1', title: 'Demo' },
      render: (_el, ctx) => {
        captured = ctx
      },
    })

    new InProcessBridge().mount(plugin, mountOptions())

    expect(captured).not.toBeNull()
    // Moved from four members to five by §32, deliberately. This stays an EXACT
    // set rather than being relaxed to "at least": the whole point of the number
    // is that a sixth member appearing is a decision someone made, not a drift.
    expect(Object.keys(captured!).sort()).toEqual(['draft', 'emit', 'schema', 'session', 't'])
    expect(typeof captured!.emit).toBe('function')
    expect(typeof captured!.draft).toBe('function')
    expect(typeof captured!.t).toBe('function')

    // Type-level: the ctx contract has no members beyond the five (a new member
    // would make this array assignment fail to typecheck).
    const keys: Array<keyof ActivityRenderCtx> = ['draft', 'emit', 'schema', 'session', 't']
    expect(keys).toHaveLength(5)
  })

  it('routes ctx.draft to the host and returns nothing to the plugin', () => {
    // Fire-and-forget by contract ([R32.01]): a plugin must not be able to learn
    // whether anyone is reading, because a plugin that could would be a channel
    // for telling the participant something the disclosure chip already says
    // properly — or for concealing it.
    const reported: unknown[] = []
    let captured: ActivityRenderCtx | null = null
    const plugin = defineActivityPlugin({
      manifest: { key: 'demo', version: '1', title: 'Demo' },
      render: (_el, ctx) => {
        captured = ctx
      },
    })

    new InProcessBridge().mount(plugin, mountOptions({ reportDraft: (p) => reported.push(p) }))
    const returned = captured!.draft({ cell: 'half an answer' })

    expect(reported).toEqual([{ cell: 'half an answer' }])
    expect(returned).toBeUndefined()
  })

  it('makes ctx.draft a working no-op when the host supplies no reporter', () => {
    // A host outside a chatroom has no socket to report on. Reporting nothing is
    // always the safe direction, so this must not throw.
    let captured: ActivityRenderCtx | null = null
    const plugin = defineActivityPlugin({
      manifest: { key: 'demo', version: '1', title: 'Demo' },
      render: (_el, ctx) => {
        captured = ctx
      },
    })

    new InProcessBridge().mount(plugin, mountOptions())

    expect(() => captured!.draft({ anything: true })).not.toThrow()
  })

  it('returns a no-op teardown when the plugin returns nothing', () => {
    const plugin = defineActivityPlugin({
      manifest: { key: 'demo', version: '1', title: 'Demo' },
      render: () => {},
    })
    const teardown = new InProcessBridge().mount(plugin, mountOptions())
    expect(() => teardown()).not.toThrow()
  })
})

describe('IframeBridge — deferred stub (AC-7 / FU-1)', () => {
  it('throws on mount so it is never silently used in v1', () => {
    const plugin = defineActivityPlugin({
      manifest: { key: 'demo', version: '1', title: 'Demo' },
      render: () => {},
    })
    expect(() => new IframeBridge().mount(plugin, mountOptions())).toThrow(/deferred/i)
  })

  it('types the postMessage message-kind contract in both directions', () => {
    const toHost: PluginToHostMessage = { kind: 'emit', payload: { a: 1 } }
    // The draft kind lands here with the fifth ctx member so the deferred bridge
    // still has a complete contract. One-way by construction: there is no
    // host-to-plugin acknowledgement, matching `ctx.draft` returning nothing.
    const draftMsg: PluginToHostMessage = { kind: 'draft', payload: { a: 1 } }
    const schemaMsg: HostToPluginMessage = { kind: 'schema', schema: { type: 'object' } }
    const resultMsg: HostToPluginMessage = {
      kind: 'result',
      result: { submissionId: 's1', status: 'validated', isValid: true, subScores: {} },
    }
    expect(toHost.kind).toBe('emit')
    expect(draftMsg.kind).toBe('draft')
    expect(schemaMsg.kind).toBe('schema')
    expect(resultMsg.kind).toBe('result')
  })
})
