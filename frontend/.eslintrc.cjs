/**
 * SoC-first ESLint config (see §24.15 — 12 CI gates).
 *
 * Phase A: stubs + dependency-direction rule set to *warn* so the skeleton
 * lints clean. Phase J ratchets them to *error* and adds the remaining
 * gates (no-global-css, v-html allowlist, bundle budgets, etc).
 */
/** @type {import('eslint').Linter.Config} */
module.exports = {
  root: true,
  parser: 'vue-eslint-parser',
  parserOptions: {
    parser: '@typescript-eslint/parser',
    ecmaVersion: 'latest',
    sourceType: 'module',
    extraFileExtensions: ['.vue'],
  },
  plugins: ['@typescript-eslint', 'boundaries'],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:vue/vue3-recommended',
  ],
  settings: {
    'boundaries/elements': [
      { type: 'app', pattern: 'src/app/**' },
      { type: 'slice', pattern: 'src/slices/*', mode: 'folder' },
      { type: 'shared', pattern: 'src/shared/**' },
    ],
  },
  rules: {
    // ---- Phase A: keep gentle; Phase J tightens to `error`. ----
    'boundaries/element-types': ['warn', {
      default: 'disallow',
      rules: [
        { from: 'app',    allow: ['slice', 'shared'] },
        // Dependency direction per §24.2:
        //   conversation → agents → keys → tenancy → identity → shared
        { from: 'slice',  allow: ['shared'] },
        { from: 'shared', allow: ['shared'] },
      ],
    }],
    'no-restricted-imports': ['warn', {
      patterns: [
        { group: ['**/slices/*/infrastructure/*'], message: 'Import from slice index only.' },
      ],
    }],
    'vue/multi-word-component-names': 'off',
    '@typescript-eslint/consistent-type-imports': ['warn', { prefer: 'type-imports' }],
    '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
  },
  overrides: [
    {
      files: ['**/__tests__/**/*', '**/*.spec.ts', '**/*.test.ts'],
      rules: { '@typescript-eslint/no-explicit-any': 'off' },
    },
  ],
  ignorePatterns: ['dist', 'node_modules', '.vite', 'src/shared/api-client/**'],
}
