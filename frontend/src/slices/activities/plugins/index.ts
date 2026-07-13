// First-party plugin registration entry point. Importing this module wires
// whatever bundled plugins exist. v1 ships none here — the project's canvas
// plugin (out of scope for this SDK task) registers itself against
// `registerActivityPlugin`; the SDK and host are complete without it.

export { getActivityPlugin, registerActivityPlugin, clearActivityPlugins } from './registry'
