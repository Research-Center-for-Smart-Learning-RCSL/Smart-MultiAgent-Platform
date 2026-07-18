// Single dynamic-import boundary for the JSON build of CodeMirror 6 (see the
// 2026-07-16-code-editor-syntax-highlighting task spec, Q-5). Everything this
// module statically imports lands in one lazy chunk, measured at 150 911 B gz
// (2026-07-17) against the 204 800 B lazy-chunk budget in
// scripts/check-bundle-size.sh. Statically importing a second
// @codemirror/lang-* grammar here — or anywhere else reached only through this
// module — pushes the combined chunk to 258 515 B gz, FAILING the budget by
// 26%. Each additional grammar must get its own sibling module and its own
// dynamic import() call in SCodeEditor.vue, never a static import alongside
// this one.
export { EditorView, lineNumbers, keymap, placeholder } from '@codemirror/view'
export { EditorState, Compartment } from '@codemirror/state'
export { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
export { syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'
export { json, jsonParseLinter } from '@codemirror/lang-json'
export { linter, lintGutter } from '@codemirror/lint'
