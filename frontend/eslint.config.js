/**
 * SoC-first ESLint flat config (§24.15 — 12 CI gates).
 *
 * Phase J: all boundary rules at *error*; cross-slice dependency direction
 * enforced; transport isolation gate; no-alert.
 */
import pluginVue from 'eslint-plugin-vue'
import boundaries from 'eslint-plugin-boundaries'
import tseslint from '@typescript-eslint/eslint-plugin'
import tsParser from '@typescript-eslint/parser'
import vueParser from 'vue-eslint-parser'
import vuejsAccessibility from 'eslint-plugin-vuejs-accessibility'

const SLICES = ['identity', 'tenancy', 'keys', 'agents', 'conversation', 'workflow', 'admin']

const SLICE_DEPS = {
  identity:     [],
  tenancy:      ['identity'],
  keys:         ['tenancy', 'identity'],
  agents:       ['keys', 'tenancy', 'identity'],
  conversation: ['agents', 'keys', 'tenancy', 'identity'],
  workflow:     ['conversation', 'agents', 'keys', 'tenancy', 'identity'],
  admin:        [],
}

function buildSliceBoundaryRules() {
  return SLICES.map((slice) => ({
    from: [['slice', { slice }]],
    allow: [
      'shared',
      ...SLICE_DEPS[slice].map((dep) => ['slice', { slice: dep }]),
    ],
  }))
}

function buildNoRestrictedImports() {
  const patterns = []
  for (const slice of SLICES) {
    patterns.push({
      group: [
        `@slices/${slice}/api/*`,
        `@slices/${slice}/stores/*`,
        `@slices/${slice}/queries/*`,
        `@slices/${slice}/composables/*`,
        `@slices/${slice}/components/*`,
        `@slices/${slice}/views/*`,
        `@slices/${slice}/types/*`,
        `@slices/${slice}/lib/*`,
        `**/slices/${slice}/api/*`,
        `**/slices/${slice}/stores/*`,
        `**/slices/${slice}/queries/*`,
        `**/slices/${slice}/composables/*`,
        `**/slices/${slice}/components/*`,
        `**/slices/${slice}/views/*`,
        `**/slices/${slice}/types/*`,
        `**/slices/${slice}/lib/*`,
      ],
      message: `Cross-slice import must go through @slices/${slice}/index.ts (R24.05).`,
    })
  }
  return patterns
}

export default [
  // Global ignores
  {
    ignores: ['dist/**', 'node_modules/**', '.vite/**', 'src/shared/api-client/**'],
  },

  // Base Vue recommended (flat config)
  ...pluginVue.configs['flat/recommended'],

  // Main config for all TS / Vue files
  {
    files: ['src/**/*.ts', 'src/**/*.vue'],
    plugins: {
      '@typescript-eslint': tseslint,
      boundaries,
      'vuejs-accessibility': vuejsAccessibility,
    },
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tsParser,
        ecmaVersion: 'latest',
        sourceType: 'module',
        extraFileExtensions: ['.vue'],
      },
    },
    settings: {
      'boundaries/elements': [
        { type: 'app', pattern: ['src/app/**'] },
        {
          type: 'slice',
          pattern: ['src/slices/*'],
          mode: 'folder',
          capture: ['slice'],
        },
        { type: 'shared', pattern: ['src/shared/**'] },
      ],
      'boundaries/ignore': ['src/shared/api-client/**'],
    },
    rules: {
      ...tseslint.configs.recommended.rules,

      // ---- Gate #1: Layer direction ----
      'boundaries/element-types': ['error', {
        default: 'disallow',
        rules: [
          { from: 'app', allow: ['slice', 'shared'] },
          ...buildSliceBoundaryRules(),
          { from: 'shared', allow: ['shared'] },
        ],
      }],

      // ---- Gate #2: Slice isolation — cross-slice imports via index.ts only ----
      'no-restricted-imports': ['error', {
        patterns: buildNoRestrictedImports(),
      }],

      // ---- Gate #3: Transport isolation ----
      'no-restricted-globals': ['error',
        { name: 'fetch', message: 'Use @shared/transport — gate #3.' },
        { name: 'WebSocket', message: 'Use @shared/transport/ws-manager — gate #3.' },
        { name: 'EventSource', message: 'Use @shared/transport — gate #3.' },
      ],

      // ---- Gate #5: No alert / confirm / prompt ----
      'no-alert': 'error',

      // ---- Gate #4: v-html only in renderMarkdown.ts ----
      'vue/no-v-html': 'error',

      // ---- Gate #12: i18n — no bare string literals in .vue templates ----
      'vue/no-bare-strings-in-template': ['error', {
        allowlist: [
          '—', '…', '⚙', '#', '(', ')', ',', '.', ':', '/', '%', '*', '+', '-',
          '&nbsp;', '|', '×', '→', '←', '↶', '↷', '·', '★',
          '[', ']', '▶',
          'SMAP', 'ID', 'UUID', 'CIDR', 'JSON', 'CSV', 'URL', 'API',
          'OpenAI', 'Claude', 'Gemini', 'Brave', 'Serper', 'Tavily', 'Google CSE',
          'Yes', 'No',
          '404', 'v',
        ],
        attributes: {
          '/.+/': ['title', 'aria-label', 'aria-placeholder', 'aria-roledescription', 'aria-valuetext'],
          'input': ['placeholder'],
          'img': ['alt'],
        },
        directives: ['v-text'],
      }],

      // ---- Gate #11: Accessibility ----
      'vuejs-accessibility/no-role-button-on-non-buttons': 'error',
      'vuejs-accessibility/label-has-for': ['error', { required: { some: ['nesting', 'id'] } }],
      'vuejs-accessibility/form-control-has-label': 'error',
      'vuejs-accessibility/anchor-has-content': 'error',
      'vuejs-accessibility/click-events-have-key-events': 'error',
      'vuejs-accessibility/mouse-events-have-key-events': 'error',
      'vuejs-accessibility/no-autofocus': 'warn',
      'vuejs-accessibility/no-distracting-elements': 'error',
      'vuejs-accessibility/tabindex-no-positive': 'error',

      // ---- General TS ----
      'vue/multi-word-component-names': 'off',
      '@typescript-eslint/consistent-type-imports': ['warn', { prefer: 'type-imports' }],
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
    },
  },

  // Slice-internal files may import freely within their own slice
  ...SLICES.map((slice) => ({
    files: [`src/slices/${slice}/**/*.ts`, `src/slices/${slice}/**/*.vue`],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: buildNoRestrictedImports().filter(
          (p) => !p.message.includes(`@slices/${slice}/`),
        ),
      }],
    },
  })),

  // Transport + slice api/ may use axios, WebSocket, fetch
  {
    files: ['src/shared/transport/**/*.ts', 'src/slices/*/api/**/*.ts'],
    rules: {
      'no-restricted-globals': 'off',
    },
  },

  // Gate #4 override: v-html allowed only in conversation views that use renderMarkdown
  {
    files: [
      'src/slices/conversation/views/ChatroomView.vue',
    ],
    rules: {
      'vue/no-v-html': 'off',
    },
  },

  // Gate #12 override: shared/ui atoms may use bare strings (design-system labels)
  {
    files: ['src/shared/ui/**/*.vue'],
    rules: {
      'vue/no-bare-strings-in-template': 'off',
    },
  },

  // Test files
  {
    files: ['**/__tests__/**/*', '**/*.spec.ts', '**/*.test.ts'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      'no-restricted-imports': 'off',
    },
  },
]
