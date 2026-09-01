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
  // import conversation back (Q-3: the dependency is one-way). It does depend
  // downward on agents (agent pickers in activity-type forms) and tenancy
  // (project-role gating).
  activities:    ['agents', 'tenancy'],
  // conversation sits ABOVE workflow: the chatroom hosts workflow UI
  // (ApprovalCard, DlqViewer) and calls its approval/wakeup api. workflow must
  // NOT import conversation back — it wraps the read-only workspace/chatroom
  // lookups it needs in its own api/ instead.
  conversation:  ['workflow', 'activities', 'agent-groups', 'agents', 'keys', 'tenancy', 'identity'],
  workflow:      ['agent-groups', 'agents', 'keys', 'tenancy', 'identity'],
  admin:         ['prompt-studio', 'skills'],
  notifications: ['identity'],
  'prompt-studio': ['keys'],
  // The skills slice mounts a per-agent binding panel inside `agents` and a platform
  // view inside `admin`; it consumes only shared. `keys` is declared for the future
  // description-assistant reuse (Q-14) and is otherwise inert.
  skills:        ['keys'],
}

// eslint-plugin-boundaries v7 selector shape: an entity selector wraps an
// element selector, and captured values live under `captured` instead of being
// the second member of a ['type', {...}] tuple. v7 still accepts the v5 tuple
// through a compatibility path, but only after warning "unrecognized selector
// shape" — and that path goes away in the next major, which would silently
// drop gate #1. See docs at https://www.jsboundaries.dev/docs/selectors/.
const sliceElement = (slice) => ({ element: { type: 'slice', captured: { slice } } })

// Every `allow`/`disallow` entry must name the side it constrains. 7.2.0 stopped
// reading a bare element selector as an implicit `to`: instead of erroring it
// warns and matches nothing, so gate #1 flips from "allows the declared imports"
// to "rejects every import" — enforcement in neither direction. That is what
// `check:boundaries-enforced` (gate #1b) catches, and it is why the wrapper is
// not optional cosmetics.
const to = (selector) => ({ to: selector })

function buildSliceBoundaryPolicies() {
  return SLICES.map((slice) => ({
    from: [sliceElement(slice)],
    allow: [
      to({ element: { type: 'shared' } }),
      ...SLICE_DEPS[slice].map((dep) => to(sliceElement(dep))),
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
  // `e2e-csp/` is a second suite rather than a spec in `e2e/`: it runs against a
  // built bundle behind nginx, so `playwright.config.ts` (Vite baseURL, testDir
  // `./e2e`) must not pick it up.
  {
    files: ['e2e/**/*.ts', 'e2e-csp/**/*.ts'],
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

  // ---- Gate #13: never branch on the generated client's ApiError ----
  // `transport/axios.ts` registers its problem+json rejection handler on the
  // bare axios singleton the generated services call, so every problem+json
  // failure — which is every error the SMAP backend itself produces — is
  // already a `@shared/errors` instance before `core/request.ts` could raise
  // its own same-named class. An `instanceof` against the generated one is
  // false for all of them: dead recovery code that a test constructing the
  // same wrong class cannot expose (R24.35).
  //
  // Not *strictly* unreachable, and the rule is worded for what is true. A
  // response that is not problem+json (an nginx 413/502/504 HTML page, or any
  // layer in front of `register_exception_handlers`) falls through
  // `axios.ts:201` as a raw AxiosError, and `core/request.ts:228,266` then
  // re-raises it as the generated class. Catching it there is still the wrong
  // fix — the transport should be normalising those too, which is FU-6 — so
  // this rule stays an error and the gap is named rather than carved out.
  //
  // `no-restricted-syntax`, not `no-restricted-imports`, because the test
  // override at the bottom of this file turns the latter off wholesale for
  // unrelated cross-slice fixtures — and a test asserting on the unreachable
  // class is precisely how both dead branches stayed green.
  //
  // Its own config object, with the widest `files` list in this file, because
  // the class must not be named from anywhere: `tests/` (fixtures, MSW
  // handlers) and `e2e/` are outside the `src/**` block below and were
  // otherwise not linted at all. A guard with a hole exactly where the
  // original defect lived is the failure mode this whole task is about.
  //
  // Known gap (Q-4a): `import * as c from '@shared/api-client'` then
  // `c.ApiError` escapes both selectors. Catching it needs a
  // `MemberExpression[property.name='ApiError']` selector that would fire on
  // every correct `@shared/errors` use too, and a rule that punishes the class
  // it is steering people toward gets disabled. Left to review.
  {
    files: [
      'src/**/*.ts',
      'src/**/*.vue',
      'tests/**/*.ts',
      'e2e/**/*.ts',
      'e2e-csp/**/*.ts',
    ],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tsParser,
        ecmaVersion: 'latest',
        sourceType: 'module',
        extraFileExtensions: ['.vue'],
      },
    },
    rules: {
      // Both selectors match the barrel AND any deep path under it
      // (`@shared/api-client/core/ApiError` resolves perfectly well through the
      // alias). Pinning the exact barrel specifier left that route open while
      // the gate went on reporting itself healthy — gate #7 below already
      // anticipates the same deep path with its `@shared/api-client/*` pattern.
      'no-restricted-syntax': ['error',
        {
          selector:
            'ImportDeclaration[source.value=/^@shared\\/api-client(\\/|$)/] > ImportSpecifier[imported.name="ApiError"]',
          message:
            'The generated ApiError is not the class the transport throws — import ApiError from @shared/errors (gate #13).',
        },
        {
          // Closes the re-export route: a slice barrel could otherwise launder
          // the same class into a name that passes the selector above.
          selector:
            'ExportNamedDeclaration[source.value=/^@shared\\/api-client(\\/|$)/] > ExportSpecifier[local.name="ApiError"]',
          message:
            'Re-exporting the generated ApiError spreads the wrong class — use @shared/errors (gate #13).',
        },
      ],
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
      // Without a TS-aware resolver, `@slices/*` / `@shared/*` aliases are
      // unresolvable and eslint-plugin-boundaries silently treats every aliased
      // import as external — making gate #1 blind to alias-based cross-slice
      // imports. Do not remove.
      'import/resolver': {
        typescript: {
          project: ['tsconfig.app.json'],
        },
      },
      'boundaries/elements': [
        { type: 'app', pattern: ['src/app/**'] },
        {
          type: 'slice',
          pattern: ['src/slices/*'],
          capture: ['slice'],
        },
        { type: 'shared', pattern: ['src/shared/**'] },
      ],
      'boundaries/ignore': ['src/shared/api-client/**', 'src/shared/stores/session.ts'],
    },
    rules: {
      ...tseslint.configs.recommended.rules,

      // ---- Gate #1: Layer direction ----
      'boundaries/dependencies': ['error', {
        default: 'disallow',
        policies: [
          {
            from: [{ element: { type: 'app' } }],
            allow: [to({ element: { type: 'slice' } }), to({ element: { type: 'shared' } })],
          },
          ...buildSliceBoundaryPolicies(),
          {
            from: [{ element: { type: 'shared' } }],
            allow: [to({ element: { type: 'shared' } })],
          },
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

  // ---- Gate #7: Store isolation — stores hold client state only ----
  // Pinia stores must not call the API layer directly: server state belongs to
  // queries/ (TanStack Query); stores subscribe to queries or accept values
  // from views. Blocks both same-slice relative imports and cross-slice paths.
  {
    files: ['src/slices/*/stores/**/*.ts'],
    rules: {
      // Merged with the gate #2 patterns because this override REPLACES the
      // per-slice no-restricted-imports config for store files.
      'no-restricted-imports': ['error', {
        patterns: [
          ...buildNoRestrictedImports(),
          {
            group: ['**/api', '**/api/*', '@shared/api-client', '@shared/api-client/*'],
            message: 'Stores hold client state only — server state goes through queries/ (gate #7).',
          },
        ],
      }],
    },
  },

  // Gate #7 allowlist: the session store OWNS the auth lifecycle (login,
  // logout, silent refresh, hydrate-at-boot) which must run outside component
  // and query context (route guards, axios interceptors) — so it calls
  // authApi directly. It is the single sanctioned store→api edge; do not add
  // files here without the same justification.
  {
    files: ['src/slices/identity/stores/session.ts'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: buildNoRestrictedImports().filter(
          (p) => !p.message.includes('@slices/identity/'),
        ),
      }],
    },
  },

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
