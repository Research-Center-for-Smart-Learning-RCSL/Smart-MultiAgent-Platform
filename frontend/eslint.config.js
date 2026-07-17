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

const SLICES = ['identity', 'tenancy', 'keys', 'agents', 'agent-groups', 'activities', 'conversation', 'workflow', 'admin', 'notifications', 'prompt-studio', 'skills']

// [R24.06] Cross-slice dependency direction. `agent-groups` is a first-class,
// project-scoped slice (agent-group CRUD, membership, and the group-owned Concept
// Map panel) sitting between conversation and agents in the chain; it imports
// agents/keys/tenancy/identity/shared and is imported by conversation.
const SLICE_DEPS = {
  identity:      [],
  tenancy:       ['identity'],
  keys:          ['tenancy', 'identity'],
  agents:        ['skills', 'prompt-studio', 'keys', 'tenancy', 'identity'],
  'agent-groups': ['agents', 'keys', 'tenancy', 'identity'],
  // activities renders inside the chatroom via a plugin host but must never
  // import conversation back (Q-3: the dependency is one-way). shared only.
  activities:    [],
  conversation:  ['activities', 'agent-groups', 'agents', 'keys', 'tenancy', 'identity'],
  workflow:      ['conversation', 'agent-groups', 'agents', 'keys', 'tenancy', 'identity'],
  admin:         ['prompt-studio', 'skills'],
  notifications: ['identity'],
  'prompt-studio': ['keys'],
  // The skills slice mounts a per-agent binding panel inside `agents` and a platform
  // view inside `admin`; it consumes only shared. `keys` is declared for the future
  // description-assistant reuse (Q-14) and is otherwise inert.
  skills:        ['keys'],
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

  // Playwright e2e tests — needs the TS parser for non-null assertions etc.
  {
    files: ['e2e/**/*.ts'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
    },
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
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
      'boundaries/ignore': ['src/shared/api-client/**', 'src/shared/stores/session.ts'],
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
          '[', ']', '▶', '▍',
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
      // Spec (REQUIREMENTS §24.15) calls for "no role=button on non-buttons" —
      // eslint-plugin-vuejs-accessibility has no such rule. no-static-element-interactions
      // covers the same intent: it flags click/key handlers on non-interactive elements,
      // which is the underlying anti-pattern that role="button" on a <div> exemplifies.
      'vuejs-accessibility/no-static-element-interactions': 'error',
      'vuejs-accessibility/interactive-supports-focus': 'error',
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
      '@typescript-eslint/no-unused-vars': ['warn', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
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

  // Gate #4 override: v-html is allowed ONLY in the files listed below, each of
  // which passes all markup through renderMarkdown()/sanitizeSnippet() → DOMPurify
  // before binding. Any other file that needs v-html must be explicitly added here
  // with the same sanitisation guarantee — do not expand this list without a
  // security review.
  {
    files: [
      'src/slices/conversation/views/ChatroomView.vue',
      // Message rendering was extracted out of ChatroomView into these
      // presentational components (07-conversation). Each binds ONLY the
      // output of renderMarkdown()/sanitizeSnippet() → DOMPurify, preserving
      // the same single-sanitiser XSS contract. Do not add files here without
      // that guarantee + a security review.
      'src/slices/conversation/components/ChatroomMessageBubble.vue',
      'src/slices/conversation/components/ChatroomStreamingBubble.vue',
      'src/slices/conversation/components/ChatroomSearchPanel.vue',
      // Observer analyses (SRS §28) render through the same renderMarkdown()
      // → DOMPurify pipeline; same single-sanitiser contract.
      'src/slices/conversation/components/ObservationCard.vue',
    ],
    rules: {
      'vue/no-v-html': 'off',
    },
  },

  // shared/ui atom overrides:
  // - require-default-prop: these atoms are TS-typed via withDefaults and already
  //   supply a real default for every prop that needs one (booleans -> false,
  //   variant/size -> a concrete value). The props this rule flags are all
  //   intentionally-optional value props (title?, id?, accept?, sortBy?, ...) whose
  //   default IS undefined; TS enforces call-site correctness, so the runtime rule
  //   only adds noise in this layer. It stays on for every slice component.
  {
    files: ['src/shared/ui/**/*.vue'],
    rules: {
      'vue/require-default-prop': 'off',
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
