---
type: feature
status: approved
created: 2026-08-21
requirements: [R24.28, R24.30, R24.34, R24.48, R24.49]
depends_on: [2026-08-21-visual-refinement-phase1-token-adoption]
---

# Visual refinement phase 2: typeface, neutral axis, surface depth, and press feedback

## 1. Summary

The frontend is internally consistent and reads as flat and generic. Five causes, each
independently verifiable in the code: the typeface is the platform default, the neutral
greys are drawn from two different hue axes, a card and the page behind it are the same
colour so nothing reads as layered, the interior of the app is divided by a single
undifferentiated 1px rule, and no element in the entire codebase has a pressed state. This
dossier changes the visual identity itself: it self-hosts a variable Latin webfont, moves
every neutral onto one axis, introduces a canvas-versus-surface distinction so raised
elements are actually raised, splits the border token into boundary and interior weights,
loosens table density, and gives interactive elements a press state and a focus ring that
works on every background. It is deliberately an identity change, which
`2026-07-05-sitewide-ui-enhancement` ruled out at its §2 ("No redesign of the visual
identity (colors, logo, typography family stay)") and which is why the product looks the way
it does.

It depends on `2026-08-21-visual-refinement-phase1-token-adoption` for a real reason, not an
overlap one: the changes below are almost entirely token-value edits, and they only reach
the product once the components consume tokens.

## 2. Goals and Non-goals

**Goals**

- The application does not render in the platform default UI font on any platform.
- Every neutral colour in both themes sits on one hue axis.
- A card, a modal, a dropdown and the top bar read as sitting above the page, without
  relying on a 1px border to say so.
- The interior of a dense surface (table rows, card sections, accordion items) is separated
  more lightly than the outer boundary of a control or container.
- Every interactive element has a visible pressed state, and the focus ring is correct on
  every background it can appear over.
- Both themes keep WCAG 2.1 AA contrast, which `docs/UI/01-design-system.md:100` already
  requires of every component ("Meet WCAG 2.1 AA") and which nothing currently measures.

**Non-goals**

- **No layout change.** No spacing, column, grid or breakpoint change other than the table
  row height in §6.5. The 34 padded view roots and the missing scroll reset belong to
  `2026-08-19-content-area-spacing-and-scroll-contract`; the page width policy belongs to
  that dossier's FU-4 and is explicitly not decided here (Q-9).
- **No new component and no `S*` prop change**, except `SCard`'s default variant gaining a
  resting elevation, which is a value change behind the existing `variant` prop.
- **No accent-colour change.** `--color-accent: #2563eb` / `#60a5fa` and the four status
  colours stay. The brand hue is not in question; the neutrals around it are.
- **No logo, wordmark or landing-page redesign.** `AppTopBar.vue:42-47` stays a text
  wordmark and `Landing.vue` is not restyled. Both will shift because the tokens beneath
  them shift; §10 makes not breaking them a verification item, not a design item.
- **No new npm dependency.** The font ships as `woff2` files under `frontend/public/fonts/`,
  not as an `@fontsource/*` package (Q-3).
- **No motion added or removed.** [R24.49]'s motion language stands as written except for
  the pressed state amended into it in §13.
- **No dark-mode-only or light-mode-only design.** Every change lands in both themes in the
  same commit.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is this one dossier with phase 1, or separate? | **Separate**, and this one depends on phase 1 logically. | Phase 1's acceptance bar is "zero rendered difference" and this one's purpose is a rendered difference; a single dossier could assert neither. And the substance: today a `--font-size-sm` edit changes three of 46 components, so shipping this dossier first would produce a half-restyled product. |
| Q-2 | Keep the system font stack and fix only its metrics, or ship a webfont? | **Ship a webfont: Inter, variable weight, self-hosted.** | The system stack (`main.css:53-54`) renders as Segoe UI on Windows, San Francisco on macOS and Roboto on Android, so the product has three different typographic identities and none of them is chosen. Fixing tracking and OpenType features on top of that improves each one but leaves the product looking like whatever OS it is on, which is the largest single contributor to the reported "generic" quality. Inter is chosen over Geist, IBM Plex Sans and Public Sans because it is the closest to a neutral UI grotesque, has a genuine variable axis so weight 500/600/700 need one file, and is SIL OFL 1.1 so bundling it in a self-hosted product carries no licence question. |
| Q-3 | Self-host or a font CDN? | **Self-host, and it is not a preference.** `deploy/compose/nginx/conf.d/smap.conf:177` sets `font-src 'self' data:`, so a Google Fonts or CDN reference is blocked by the deployed CSP. Files go in `frontend/public/fonts/`, referenced by an `@font-face` block in `main.css`. | Loosening the CSP to admit a third-party font host on a product whose whole premise is self-hosting would be the wrong trade, and would need a security review for a purely cosmetic gain. Self-hosting is also the only option that works for an air-gapped install. |
| Q-4 | How does the webfont interact with the zh-TW UI? | **The `@font-face` carries a `unicode-range` restricted to Latin and Latin-Extended, and the CJK families are named explicitly after Inter in the stack.** CJK never attempts to resolve through Inter and never triggers a download for a CJK-only screen. | Inter has no CJK coverage, so without `unicode-range` a browser may still fetch the file to discover that, and CJK falls to an unnamed browser default today (`main.css:53-54` names no CJK family at all). Naming them is a change to CJK rendering and is therefore in scope here rather than in phase 1. |
| Q-5 | Which neutral axis? | **Slate.** Light theme moves `--color-muted`, `--color-border`, `--color-neutral-tint`, `--color-neutral-on`, `--color-sidebar-text`, `--color-sidebar-section-text` and `--color-fg` from Tailwind `gray` onto `slate`; dark theme moves `--color-surface`, `--color-border`, `--color-muted`, `--color-fg`, `--color-sidebar-bg`, `--color-sidebar-text`, `--color-neutral-tint` and `--color-neutral-on` the same way. | The split today is not arbitrary but it is invisible: the *surfaces* are already slate (`--color-surface: #f8fafc` = slate-50, `--color-sidebar-bg: #f1f5f9` = slate-100, `--color-surface-active: #e2e8f0` = slate-200) and the *text and borders* are gray (`--color-muted: #6b7280` = gray-500, `--color-border: #e5e7eb` = gray-200, `--color-neutral-tint: #f3f4f6` = gray-100). Moving text onto the surfaces' existing axis is the smaller edit and preserves the "light blue / grey" identity `docs/UI/00-overview.md:36` states, because slate is the blue-leaning grey. Moving the surfaces to gray instead would flatten the only warmth-versus-coolness decision the palette has already made. |
| Q-6 | How is layering created, given a card and the page are both `--color-bg`? | **Add a third surface role, `--color-canvas`, for the application background**, and leave `--color-bg` meaning "a raised sheet" (cards, modals, dropdowns, top bar, table rows) and `--color-surface` meaning "a recessed fill inside a sheet" (table headers, secondary buttons, card footers). `SCard`'s `default` variant gains `--elevation-1`. | This is the root cause of the flatness and it is structural, not a shadow-tuning problem: `AppShell.vue:186` paints the content area `var(--color-bg)` and `SCard.vue:41-46` paints the card `var(--color-bg)` with `--elevation-0`, so the two are the same colour with a 1px line between them. No shadow value can fix a same-colour-on-same-colour relationship. Introducing a canvas role also fixes the light-versus-dark asymmetry in one move (Q-7). The alternative, redefining `--color-bg` to mean "canvas" and adding a token for the sheet, was rejected: `--color-bg` appears in `--focus-ring` (`main.css:89`), in `html/body` (`:150`) and in roughly 20 component rules, and flipping its meaning would silently invert every one of them. |
| Q-7 | The two themes have opposite separation problems. How much? | Computed from the hexes at `main.css:9,16` and `:180,187`: light goes `#ffffff` (L\* 100) to `#f8fafc` (L\* ~98.4), a gap of about **1.6 points**; dark goes `#0b0f14` (L\* ~5) to `#1f2937` (L\* ~16), a gap of about **11 points**. **Target both at 3 to 5 points** by moving the canvas rather than the sheet: light canvas becomes slate-50 against a white sheet, dark canvas becomes a slate-tinted near-black against a lifted slate-900 sheet. | Equalising the two is what makes a single set of elevation tokens read correctly in both themes, which is the premise `--elevation-0..3` (`main.css:133-136`) was built on and has never been true. Numbers are stated as computed from the cited hexes, not cited from a document. |
| Q-8 | Remove borders, or change them? | **Split the token, do not remove.** Add `--color-border-subtle` for *interior* separators (table row rules, card header/footer rules, accordion item rules, dropdown dividers) and keep `--color-border` for the *outer boundary* of a control or container (input, card, sidebar edge, top bar). | A blanket border removal is the obvious reading of "the page is cut into a grid" and it is wrong for this product: SMAP's densest screens are data tables, and row rules are what makes a wide row scannable. The real defect is that one weight does two jobs, so the boundary of a card and the rule between two table rows are the same line. Two weights fixes the reading without costing scanability. |
| Q-9 | Does this dossier decide the page width policy? | **No.** `2026-08-19-content-area-spacing-and-scroll-contract`'s Q-15, Q-16 and FU-4 record that six views cap their own width and the rest do not, with no intent source, and route the answer to `docs/UI/12-shared-patterns.md`. Nothing here changes a `max-w-*`. | Deciding it as a side effect of a colour and typography change is exactly the accidental decision that dossier's Q-16 refused to make in the opposite direction. It is a real open question (FU-2 carries it), but it is a layout question and this dossier's non-goals exclude layout. |
| Q-10 | What replaces the two-layer `box-shadow` focus ring? | **`outline: 2px solid var(--color-accent); outline-offset: 2px;`** | `--focus-ring` (`main.css:89`) is `0 0 0 2px var(--color-bg), 0 0 0 4px var(--color-accent)`: the inner ring is painted in the page background colour regardless of what is actually behind the element, so a focused control inside the sidebar (`--color-sidebar-bg`) or a card footer (`--color-surface`) gets a white halo that belongs to neither. `outline-offset` leaves a real gap that shows the actual backdrop, needs no per-container configuration, and collapses the forced-colors special case at `:264-268` into the same mechanism instead of a parallel one. The known cost is that an `overflow: hidden` ancestor clips an outline; it clips the current box-shadow identically, so nothing regresses. |
| Q-11 | Does a pressed state count as motion under [R24.49]? | **Yes, and [R24.49] is amended to name it** (§13). The press is a 1px downward translate plus one elevation step down, at `--duration-fast`. | `:active` appears **zero times** in all of `frontend/src` (repository-wide grep), so today a button that is being held down is visually identical to one that is merely hovered. R24.49 already licenses a 1 to 2px hover lift; the press is its mirror and belongs in the same requirement rather than being an undocumented exception to it. Under `prefers-reduced-motion` the global freeze at `main.css:275-286` zeroes the transition duration, so the press snaps rather than animating, which is correct: the feedback survives, the movement does not. |
| Q-12 | 48 hard-coded hex colours remain in 16 `.vue` files. In scope? | **Partly.** The ones that are a neutral or a semantic role are converted to tokens. The ones that are a data-visualisation palette (`WorkflowNodeComponent.vue`, 14; `GraphragGraphView.vue`, 8) and the `#fff` on a filled button (`SButton.vue:176,198`) stay literal. | A neutral axis change that misses 48 literals is a half-change that shows as a hue mismatch on exactly the screens where it is least expected. But a graph node palette is a categorical scale, not a UI neutral, and forcing it through semantic tokens would make it worse. The split is by role, and §6.6 enumerates it so the boundary is checkable rather than judged per file at build time. |

## 4. Current State

Every claim below cites the file. Values marked "computed" are arithmetic on a cited hex,
not a citation.

### 4.1 Typeface

`main.css:53-54` declares `--font-body: -apple-system, BlinkMacSystemFont, "Segoe UI",
Roboto, "Helvetica Neue", Arial, sans-serif`, matching `docs/UI/00-overview.md:99`
verbatim. There is no `@font-face` anywhere in `frontend/src` and no font file in
`frontend/public/` (which contains only `favicon.svg` and `og-image.png`). No CJK family is
named. `index.html:1-29` contains no font `<link>` or `preload`. No `letter-spacing` is set
on any heading: `main.css:156-172`'s `h1`/`h2`/`h3` and `SPageHeader.vue:117-125`'s 1.5rem
title all render at the font's default tracking, which for a UI grotesque at 24px is
visibly loose.

### 4.2 Neutral axis

Light theme (`main.css:9-37`), against the Tailwind palette:

| Token | Value | Axis |
|---|---|---|
| `--color-muted` | `#6b7280` | gray-500 |
| `--color-border` | `#e5e7eb` | gray-200 |
| `--color-neutral-tint` | `#f3f4f6` | gray-100 |
| `--color-neutral-on` | `#4b5563` | gray-600 |
| `--color-sidebar-text` | `#374151` | gray-700 |
| `--color-sidebar-section-text` | `#6b7280` | gray-500 |
| `--color-surface` | `#f8fafc` | **slate**-50 |
| `--color-sidebar-bg` | `#f1f5f9` | **slate**-100 |
| `--color-surface-active` | `#e2e8f0` | **slate**-200 |

Dark theme (`main.css:180-206`) is on gray throughout: `--color-surface: #1f2937`
(gray-800), `--color-border: #374151` (gray-700), `--color-muted: #9ca3af` (gray-400),
`--color-fg: #e5e7eb` (gray-200), `--color-sidebar-bg: #111827` (gray-900).
`--color-bg: #0b0f14` is not a Tailwind value.

### 4.3 Surface depth

`AppShell.vue:186` paints the content region `background: var(--color-bg)`.
`SCard.vue:41-46` paints `default` and `bordered` `background: var(--color-bg)` with
`box-shadow: var(--elevation-0)`, and `--elevation-0` is `none` (`main.css:134`). A card and
the page behind it are therefore the same colour, separated only by
`border: 1px solid var(--color-border)`. `--elevation-1` exists (`:135`) and only
`SCard`'s `elevated` variant (`:51-54`) uses it.

Separation between page and the next surface up, computed from the cited hexes: light
`#ffffff` to `#f8fafc` is about 1.6 points of L\*; dark `#0b0f14` to `#1f2937` is about 11.

`--shadow-sm..xl` (`main.css:39-42`) are the Tailwind defaults: pure-black alpha, one or two
layers. The dark overrides (`:207-210`) raise the alpha and drop `--shadow-md`/`lg`/`xl` to
a single layer each.

### 4.4 Border weight

`--color-border` is the only neutral rule token and does both jobs. Outer boundaries:
`SCard.vue:43`, `SInput.vue:158`, `AppShell.vue:169` (sidebar right edge),
`AppTopBar.vue:78` (bottom edge), `main.css:236` (`fieldset`). Interior separators:
`STable.vue:504` (header bottom), `:561` (every row), `SCard.vue:94` (header bottom), `:101`
(footer top), `AppSidebar.vue:281-285` (divider), and the accordion and dropdown dividers
specified at `docs/UI/01-design-system.md:490,514`.

### 4.5 Table density

`STable.vue:494-505`: `th` is `padding: 8px 12px`, `font-size: 12px`,
`text-transform: uppercase`, `font-weight: 600`, `color: var(--color-muted)`, with no
`letter-spacing`. `:559-563`: `td` is `padding: 8px 12px` with a `1px` bottom rule. There is
no `font-variant-numeric` anywhere in `frontend/src`, so numeric columns render
proportionally and do not align down a column.

### 4.6 Interaction feedback

A repository-wide grep for `:active` across `frontend/src` returns **no matches**.
`SButton.vue:127-131` transitions `background`, `border-color` and `color` only, so a
variant that changed elevation or position would jump. `--focus-ring` (`main.css:89`, dark
at `:214`) hard-codes `var(--color-bg)` as its inner ring, and it is applied globally at
`:257-260` to every `:focus-visible` element, including those inside `--color-sidebar-bg`
and `--color-surface` containers.

### 4.7 Literal colours outside the token system

48 hard-coded hex values across 16 `.vue` files. The concentrations are
`WorkflowNodeComponent.vue` (14) and `GraphragGraphView.vue` (8), both graph palettes;
`RegisterView.vue` (4), `LoginView.vue` (4), `ChatroomHeader.vue` (3); and single or double
occurrences in `SButton.vue`, `SAvatar.vue`, `SCheckbox.vue`, `SPagination.vue`,
`SToggle.vue`, `Landing.vue`, `NotificationBell.vue`, `ChatroomComposer.vue`,
`ChatroomSearchPanel.vue`, `ChatroomNewMessagesPill.vue`, `ChatroomMessageBubble.vue`.

### 4.8 The intent sources that must change with the code

`docs/UI/00-overview.md` §2 (`:40-83`) is the authoritative colour table, §3 (`:87-100`) the
typography table and font stacks, §4 (`:104-122`) the spacing and radius scale.
`docs/UI/01-design-system.md` §1 (`:14-84`) restates the token block. Both are amended by
this dossier; `REQUIREMENTS.md:1960` [R24.28] is amended by §13.

## 5. Design

### Options considered

**Option A - token-value edit plus a bounded set of component rules.** Change the values in
`main.css`'s `@theme` and dark block, add three token families (`--color-canvas`,
`--color-border-subtle`, tracking), and edit only the component rules that must move because
a role changed (`AppShell`'s content background, `SCard`'s default elevation, `STable`'s
density and rule weight, `SButton`'s press state, the global focus rule). Trade-off: the
diff is small and reviewable, and it is only possible because phase 1 made the components
read tokens. It cannot express a change that is not already a token.

**Option B - per-component restyle.** Walk all 46 shared components and restyle each. Trade-off:
maximum control, but it is the pattern that produced the current state, it takes the token
layer back out of the loop, and it makes the diff unreviewable.

**Option C - adopt an external design system** (a Tailwind UI kit, shadcn-vue, or similar).
Trade-off: a known-good visual language for free, but it replaces 46 in-house components that
already carry this product's accessibility, i18n and test contracts, contradicts [R24.27]
("No external UI kit"), and would be a multi-week migration for a cosmetic goal.

### Decision

**Option A.** The premise of phase 1 is that the token layer becomes the control surface for
appearance, and this dossier is the first use of it: roughly 70% of the change is values in
one file. The component edits that remain are exactly the ones where a *role* changed rather
than a value, and each is named in §6 so the reviewer can check the list is closed.

Consciously given up: the sharper end state Option C would give. The in-house library is
structurally sound (the predecessor dossier reached the same conclusion at its §5 for the
same reason), and its 46 components carry test and a11y contracts that a swap would have to
re-earn.

Also consciously given up: an opinionated typeface. Inter is the safe neutral choice and it
will not make the product distinctive on its own. Distinctiveness would need a display face
for headings and a brand mark, which are `Landing.vue` and wordmark decisions this dossier's
non-goals exclude, and which should not be bundled with a change that has to be right across
74 views.

## 6. Detailed Changes

- **Backend** - none. **API contract** - none, no `gen:api`. **Migration** - none.
- **Deploy/config** - none. `font-src 'self' data:`
  (`deploy/compose/nginx/conf.d/smap.conf:177`) already admits a self-hosted font and is not
  edited; Q-3 depends on it staying as it is.

**Frontend:**

### 6.1 Typeface

- Add `frontend/public/fonts/InterVariable.woff2` (weight axis 100 to 900, Latin plus
  Latin-Extended subset) and `frontend/public/fonts/Inter-LICENSE.txt` (SIL OFL 1.1).
  Italic is not shipped: no `<style>` block or template in `frontend/src` sets
  `font-style: italic` on a UI surface, and omitting it halves the payload.
- `main.css`: an `@font-face` block declaring `font-family: Inter`,
  `font-weight: 100 900`, `font-display: swap`, and a `unicode-range` covering Latin and
  Latin-Extended only (Q-4).
- `--font-body` becomes `Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
  "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", "Helvetica Neue", Arial, sans-serif`.
  `--font-mono` is unchanged.
- `index.html`: a `<link rel="preload" as="font" type="font/woff2" crossorigin>` for the
  Inter file, above the module script.
- New tracking tokens in `@theme`: `--tracking-tight: -0.011em` (applied at
  `--font-size-xl` and above) and `--tracking-tighter: -0.02em` (at `--font-size-3xl`).
  Applied in `main.css`'s `h1`/`h2`/`h3` base rules and in `SPageHeader.vue`'s title.
- `body` gains `-webkit-font-smoothing: antialiased` and `text-rendering: optimizeLegibility`.

### 6.2 Neutral axis

Token-value edits in `main.css` only, per Q-5. Light: `--color-fg`, `--color-muted`,
`--color-border`, `--color-neutral-tint`, `--color-neutral-on`, `--color-sidebar-text`,
`--color-sidebar-section-text` move to their slate counterparts. Dark: `--color-fg`,
`--color-muted`, `--color-surface`, `--color-border`, `--color-sidebar-bg`,
`--color-sidebar-text`, `--color-neutral-tint`, `--color-neutral-on` likewise. The exact
target values are fixed at build time against AC-9's contrast measurement rather than
guessed here; the constraint is "the slate step whose contrast against its own background is
at least what the gray step it replaces achieves today".

### 6.3 Surface depth

- New `--color-canvas` in both themes (Q-6, Q-7), targeting 3 to 5 points of L\* below
  `--color-bg` in light and above it in dark.
- `AppShell.vue:186` `background: var(--color-bg)` becomes `var(--color-canvas)`.
  `main.css:150`'s `html, body` background follows it, so an overscroll shows the canvas
  rather than a sheet.
- `AuthLayout.vue` and `PublicLayout.vue` backgrounds are reviewed against the same role
  model in the same commit; their current values are read at build time rather than assumed
  here.
- `SCard.vue:41-46`: `default` gains `box-shadow: var(--elevation-1)`. `bordered` keeps
  `--elevation-0` and is now genuinely a distinct variant rather than a synonym for
  `default`, which `2026-07-05-sitewide-ui-enhancement`'s AC-3 already required and which
  the current code does not deliver.
- `--shadow-sm..xl` are rebuilt as multi-layer shadows tinted with the neutral axis
  (`rgba(15, 23, 42, a)` rather than `rgba(0, 0, 0, a)`) with lower per-layer alpha. Dark
  overrides keep near-black at low alpha, because a dark surface carries elevation through
  lightness rather than shadow.

### 6.4 Border weight

- New `--color-border-subtle` in both themes, one step lighter than `--color-border`.
- Retargeted to it: `STable.vue:504` and `:561`, `SCard.vue:94` and `:101`,
  `AppSidebar.vue:281-285`, `SAccordion.vue`'s item rule, `SDropdown.vue`'s divider.
- `--color-border` retained at: `SCard.vue:43`, `SInput.vue:158` and the other control
  borders, `AppShell.vue:169`, `AppTopBar.vue:78`, `main.css:236`.

### 6.5 Table density

`STable.vue`: `td` padding `8px 12px` becomes `var(--space-3) var(--space-4)` (12px 16px).
`th` keeps its 12px size and 600 weight, drops `text-transform: uppercase` in favour of
sentence case with `--tracking-tight`, and takes `--color-border-subtle` for its bottom
rule. Numeric and date columns gain `font-variant-numeric: tabular-nums`, applied through
the existing `cellType` mechanism (`STable.vue:12-33`, already carrying `'number'` and
`'date'`) rather than a new prop. `STableCards.vue` follows for the mobile branch.

This is the only layout-affecting change in the dossier, and it is called out in the
non-goals for that reason: a 24px row becomes 32px, so a table showing 20 rows in a viewport
shows about 15.

### 6.6 Interaction feedback

- `main.css:257-260`: the global `:focus-visible` rule becomes
  `outline: 2px solid var(--color-accent); outline-offset: 2px` (Q-10). `--focus-ring` is
  deleted, along with the six component rules that restate it
  (`SButton.vue:133-136`, `SInput.vue:166`, `SInput.vue:258-261`, `AppTopBar.vue:108-111`,
  `AppTopBar.vue:127-131`, `SBadge.vue:154-158`), and the forced-colors block at
  `main.css:264-268` collapses into the base rule. `SInput.vue:173-175`'s danger-coloured
  ring becomes an `outline-color` override.
- `SButton.vue`: an `:active:not(.s-btn--disabled)` rule per variant, 1px translate plus one
  elevation step down (Q-11), and `box-shadow` and `transform` added to the transition list
  at `:127-131`.
- The same press treatment on the shell's icon buttons (`AppTopBar.vue:89-102`) and on
  `SidebarNavItem.vue`.
- `--color-accent-tint-hover` (`main.css:96`, dark `:217`), which has had zero consumers
  since `2026-07-05-sitewide-ui-enhancement`'s FU-9, is either adopted here for the sidebar
  hover fill or deleted. The decision is made at build time and recorded; leaving a third
  dossier's worth of dead token is not an option.

### 6.7 Literal colours

Per Q-12: convert the neutral and semantic literals in `RegisterView.vue`,
`LoginView.vue`, `ChatroomHeader.vue`, `ChatroomComposer.vue`, `ChatroomSearchPanel.vue`,
`ChatroomNewMessagesPill.vue`, `ChatroomMessageBubble.vue`, `NotificationBell.vue`,
`SAvatar.vue`, `SCheckbox.vue`, `SPagination.vue`, `SToggle.vue` and `Landing.vue` to
tokens. Leave `WorkflowNodeComponent.vue` (14) and `GraphragGraphView.vue` (8) as
categorical palettes and `SButton.vue:176,198`'s `#fff` on a filled button. Each retained
literal gains a one-line comment stating why, so the next sweep does not re-litigate it.

### 6.8 Documentation

`docs/UI/00-overview.md` §2, §3 and §4, and `docs/UI/01-design-system.md` §1, are rewritten
to the shipped values. Phase 1 already converted these documents from literals to token
names (its AC-6), so this dossier edits the token *table*, not the per-component specs.

### 6.9 i18n

No new user-facing string. `STable`'s `th` case change is a CSS change, not a content
change: the labels already come from `$t()` at each call site and are already written in
sentence case in the locale files.

## 7. NFR Checklist

- [ ] **i18n** - no new strings (§6.9). The CJK stack addition (Q-4) changes how zh-TW
      renders, so the zh-TW locale must be visually checked, not only the en one; AC-2
      requires both.
- [ ] **Audit log** - N/A. No domain event; the diff is presentational.
- [ ] **Tenant isolation** - N/A. No endpoint touched, no backend change.
- [ ] **Error handling UX** - `SAlert`, `SEmptyState` and the skeleton surfaces all inherit
      the token changes. The status tint/on pairs are unchanged, so error and warning states
      keep their current colour; only the neutral around them moves. Contrast on tinted
      surfaces is part of AC-9.
- [ ] **Performance** - one additional network request on first load for the woff2, with
      `font-display: swap` so text paints immediately in the fallback and reflows once.
      `preload` reduces the swap window. `check-bundle-size.sh:34` iterates `"$DIST"/*.js`
      only, so gate #9 does not see the font: AC-8 states the measured size explicitly
      rather than relying on a gate that structurally cannot fail on it, because [R24.48]'s
      intent is the initial payload and a font is part of it.

## 8. Security Considerations

None beyond one item worth stating rather than assuming. The font is served from the same
origin, so `deploy/compose/nginx/conf.d/smap.conf:177`'s `font-src 'self' data:` admits it
with **no CSP change**. AC-7 requires that to be verified against a running stack rather than
reasoned about, because a CSP violation on a font is silent to the user (the page renders in
the fallback) and would otherwise ship unnoticed. No other sensitive surface is touched: no
auth, no provider keys, no tenant boundary, no WebSocket, no upload, no user-input
processing, no agent or prompt surface.

## 9. Quality Notes

**Existing debt in the touched files** (record, do not imitate, do not silently fix):

- `SInput.vue:236-244`'s eye toggle sets `width: 32px; height: 32px; min-height: 44px;
  min-width: 44px` and then relies on a negative margin, which is a touch-target hack that
  reads as contradictory. Not this dossier's defect and not fixed here.
- `SBadge.vue:120-141` sets `min-width: 44px; min-height: 44px` and then `min-width: unset;
  min-height: unset` four lines later, with a `::before` pseudo-element supplying the real
  touch target. The dead declarations should go; they are not in the Q-2 property set of
  either phase, so FU-3 carries them.
- `AppShell.vue:177-181` hard-codes `transition-delay: 300ms` to match `--transition-slow`,
  carried as `2026-07-05-sitewide-ui-enhancement`'s FU-10. `--duration-slow`
  (`main.css:77`) now exists and would fix it; out of scope, FU-4.

**Patterns to follow:**

- Token consumption: `SCard.vue` and `SEmptyState.vue`, the two components that already read
  the type and spacing tokens correctly.
- Theme-pair discipline: `main.css:8-143` and `:179-218` are the only two places a colour is
  declared; every new token must appear in both blocks in the same commit.
- Reduced-motion: the global freeze at `main.css:275-286` handles every transition
  automatically, including the new press state. No component needs its own media query
  (`usePrefersReducedMotion.ts` exists for JS-driven motion only).
- Documented-decision comments: `main.css:72-74` and `:99-100` are the house style for
  recording *why* a token is named or shaped as it is. The retained literals in §6.7 follow
  it.

**Reuse inventory** (do not write new versions of these):

`--elevation-0..3`, `--duration-fast/normal/slow`, `--ease-out-soft`, `--ease-spring`,
`--motion-rise`, `--motion-lift`, `--transition-fast/normal/slow`, `--radius-sm..full`,
`--space-1..12`, `--font-size-2xs..3xl`, `--line-tight/normal/relaxed`,
`--weight-medium/semibold/bold`, and phase 1's `--font-size-code` and `--control-h-sm/md/lg`.
`STable`'s existing `cellType` union (`STable.vue:12-33`) is the hook for tabular numerals;
do not add a prop. `useBreakpoint`, `usePrefersReducedMotion` and `useListStagger` are
unchanged and unneeded here.

## 10. Risks and Rollback

- **The landing page is styled against the old neutrals and is explicitly not redesigned
  here.** `Landing.vue`, `AgentConstellation.vue` and `ParticleField.vue` were upgraded
  separately and excluded from the predecessor program (`2026-07-05` §2). They will shift
  because the tokens beneath them shift. AC-6 makes "the landing page is not visibly broken"
  a verification item; it is the surface most likely to look wrong and least likely to be
  opened during development.
- **The webfont changes every text metric on every screen.** A label that fitted its
  container in Segoe UI may wrap in Inter. Inter runs slightly wider at the same nominal
  size, so the exposure is truncation and wrapping in fixed-width chrome: the sidebar
  (`--sidebar-width: 260px`), table headers with `white-space: nowrap`
  (`STable.vue:502`), and badge labels. AC-3 targets these three specifically.
- **The table density change costs about five visible rows per screen.** Stated in §6.5 and
  accepted; it is the one deliberate layout consequence. If it proves wrong in use, the
  fallback is `--space-2` vertical with `--space-4` horizontal, which recovers the rows and
  keeps the horizontal breathing room.
- **Deleting `--focus-ring` touches six component rules plus the global one.** A missed site
  loses its focus indicator entirely, which is an accessibility regression, not a cosmetic
  one. AC-5 requires a keyboard traversal of the C-0 surface set from phase 1, in both
  themes, not a grep.
- **The contrast budget is the real constraint on Q-5's target values.** Moving a text token
  one step on a different axis can drop below 4.5:1 against a surface that also moved. AC-9
  requires every foreground-on-background pair to be measured, not assumed, and the pairs to
  be listed.
- **`prefers-reduced-motion` and the press state.** The global freeze zeroes
  `transition-duration`, so the press snaps rather than animating. Verified, not assumed:
  AC-4 includes a reduced-motion run.
- **Rollback**: no migration, no API change, no persisted state. Reverting is `git revert`
  per commit, and the commit series in §6 is ordered so that the typeface, the palette, the
  depth model, the borders, the density and the interaction states are each independently
  revertible. The font files are additive; a revert of the `main.css` `@font-face` leaves
  them unreferenced in `public/`.

## 11. Acceptance Criteria

- [ ] AC-1: **Typeface.** On a running stack with the browser cache cold,
      `getComputedStyle(document.body).fontFamily` resolves to Inter first, the woff2 is
      requested exactly once and returns 200, and no request is made to any third-party
      host. `frontend/public/fonts/` contains the woff2 and the SIL OFL 1.1 licence text.
- [ ] AC-2: **CJK is unaffected by the Latin subset.** A zh-TW screen with mixed Latin and
      CJK renders Latin in Inter and CJK in the first available family from the CJK stack,
      and a CJK-only screen does not download the Inter file (verified in the network panel,
      both themes).
- [ ] AC-3: **No text overflow regression.** At 1440x900 and 375x812, in both locales: no
      sidebar nav label is truncated that was not truncated before, no `STable` header wraps
      or clips, and no `SBadge` label overflows its pill. Checked against the phase-1 C-0
      surface set.
- [ ] AC-4: **Press and focus.** Every `SButton` variant, the top-bar icon buttons and
      `SidebarNavItem` show a visible pressed state distinct from hover; the state survives
      under `prefers-reduced-motion: reduce` as an instant change with no movement.
      `:active` is no longer absent from `frontend/src`.
- [ ] AC-5: **Focus ring correctness.** A full keyboard traversal of the phase-1 C-0 surface
      set, in both themes, shows a focus indicator on every focusable element, with no white
      or mismatched halo on controls inside the sidebar, a card footer, a table header or a
      modal footer. `--focus-ring` no longer exists in `main.css` and no component rule
      references it (grep-verifiable).
- [ ] AC-6: **Layering is visible.** On any authenticated route, an `SCard` with the default
      variant is distinguishable from the page behind it with its border removed in devtools
      (that is, the distinction survives without the 1px rule). The measured L\* gap between
      `--color-canvas` and `--color-bg` is between 3 and 5 points in **both** themes. The
      landing page, the auth pages and the chatroom render without a visibly broken surface.
- [ ] AC-7: **CSP.** With the production nginx config in front of the stack, the font loads
      with no CSP violation in the console and `smap.conf:177` is unmodified.
- [ ] AC-8: **Payload.** The woff2's transferred size is measured and recorded in §15,
      together with the initial JS bundle size before and after. `pnpm run check:bundle-size`
      passes. If the font exceeds 120 KB the subset is narrowed before the dossier closes,
      rather than the number being accepted because gate #9 cannot see it.
- [ ] AC-9: **Contrast.** Every foreground-on-background token pair in both themes is
      measured and listed in §15 with its ratio: `fg`/`canvas`, `fg`/`bg`, `fg`/`surface`,
      `muted`/`canvas`, `muted`/`bg`, `muted`/`surface`, `sidebar-text`/`sidebar-bg`,
      `sidebar-section-text`/`sidebar-bg`, `accent`/`bg`, `neutral-on`/`neutral-tint`, and
      the four status `*-on`/`*-tint` pairs. Body text pairs meet 4.5:1; non-text boundary
      pairs (`border`/`bg`, `border-subtle`/`bg`) meet 3:1.
- [ ] AC-10: **Border roles are separated.** `--color-border-subtle` exists in both themes
      and is used by every interior separator listed in §6.4; `--color-border` is used by
      every outer boundary listed there and by no interior separator (grep-verifiable
      against that list).
- [ ] AC-11: **Table density and numerals.** `STable` `td` padding is `var(--space-3)
      var(--space-4)`, `th` is sentence case, and a column declared `cellType: 'number'` or
      `'date'` renders with `font-variant-numeric: tabular-nums` so its digits align down
      the column. `STableCards` matches.
- [ ] AC-12: **Literal colours.** The 48 hex literals of §4.7 are reduced to the retained set
      of Q-12 (the two graph palettes and `SButton`'s `#fff`), each carrying a comment
      stating why it is retained. Grep-verifiable.
- [ ] AC-13: **Documentation.** `docs/UI/00-overview.md` §2, §3 and §4 and
      `docs/UI/01-design-system.md` §1 state the shipped values, including the font stack at
      `00-overview.md:99`, which currently names the system stack verbatim.
- [ ] AC-14: **No behaviour change.** Every existing unit and component test passes without
      being edited for a visual reason. A test that asserted on `--focus-ring` or on
      `SCard`'s `elevation-0` may be updated, and each such edit is recorded as a deviation.
- [ ] AC-15: gates green on CI: `pnpm lint` (all 12, notably #6 global CSS, #11
      accessibility, #12 i18n), `pnpm typecheck`, `pnpm test`, `pnpm build`,
      `pnpm run check:bundle-size`, `pnpm run check:type-coverage`,
      `pnpm run check:boundaries-enforced`. Backend gates N/A. CI is authoritative over the
      local Windows host.
- [ ] AC-16: **Phase 1's parity harness is retargeted, not deleted.**
      `frontend/e2e/20-visual-token-parity.spec.ts` is rebaselined against the new values in
      the final commit, so a future refactor still has a computed-style net. Deleting it is
      not an acceptable resolution of its failures.

## 12. Test Plan

- **AC-1, AC-2, AC-7, AC-8**: Playwright against the compose stack with the production nginx
  overlay, reading the network log and `getComputedStyle`. New spec
  `frontend/e2e/21-typography-and-assets.spec.ts`.
- **AC-3, AC-5, AC-6**: manual, via the `frontend:verify` skill, over the phase-1 C-0 surface
  set in both themes and both locales. These are visual judgements (truncation, halo,
  "reads as layered") that no assertion expresses honestly; §15 records the result per
  surface rather than a single tick.
- **AC-4**: unit assertion that an `:active` rule exists per `SButton` variant
  (`frontend/src/shared/ui/__tests__/SButton.test.ts`, asserting on the scoped style text as
  `2026-08-19-shared-overlay-and-shell-defects`'s T-9 does for `SModal`), plus a Playwright
  press check and a reduced-motion run.
- **AC-9**: a unit test in `frontend/src/shared/styles/__tests__/contrast.test.ts` that
  parses both theme blocks from `main.css`, computes WCAG contrast for the listed pairs and
  asserts the thresholds. This is a real test, not a manual measurement, and it keeps the
  budget enforced when phase 3 or a later task edits a colour.
- **AC-10, AC-11, AC-12**: extend phase 1's source sweep
  (`frontend/src/app/__tests__/` style-text assertions) with the border-role list, the
  `STable` density values and the retained-literal allowlist.
- **AC-13**: read both documents against `main.css`. No automated check; stated as such.
- **AC-14, AC-15, AC-16**: the full gate run per commit and at dossier end.

## 13. SRS Delta

Amend §24.9 of `REQUIREMENTS.md`:

- **[R24.28]** *(amended)* Design tokens live in `frontend/src/shared/styles/main.css` in
  the Tailwind v4 `@theme` block as CSS custom properties, themed light/dark via the
  `data-theme` attribute on `<html>`. The token vocabulary covers colour (surface roles
  canvas / raised / recessed, semantic, status tint/on, and two border weights: boundary and
  interior), spacing, typography (size, weight, line-height and tracking), control height,
  radius, shadow/elevation, motion (duration/easing/distance), focus, layout, and z-index
  scales. Components consume tokens and declare no literal type, spacing, elevation or
  neutral colour value.
- **[R24.49]** *(amended)* Motion language: interface motion follows a documented
  restrained-professional spec - route transitions 150-200 ms fade plus a rise of at most
  6 px, hover elevation lift of 1 to 2 px, a pressed state of a 1 px depression and one
  elevation step down, first-load-only list stagger of at most 300 ms total, easings and
  durations from the motion tokens. All motion collapses under
  `prefers-reduced-motion: reduce`, under which a pressed state changes instantly rather
  than disappearing.
- **[R24.50]** *(new)* The interface typeface is a self-hosted variable Latin webfont served
  from the application's own origin, subset to Latin and Latin-Extended by `unicode-range`,
  loaded with `font-display: swap` and preloaded from the document head. CJK families are
  named explicitly in the font stack after it and are never resolved through it. No external
  font host is referenced: the deployed CSP is `font-src 'self' data:` and is not relaxed to
  admit one.

## 14. Open Questions

- The exact hex values for the moved neutrals, `--color-canvas` and `--color-border-subtle`
  are fixed at build time against AC-9's contrast measurement rather than named here. The
  constraint is stated (§6.2, Q-7); the values are an output of the measurement, and naming
  them in advance would invite them to be shipped unmeasured.
- Whether `--color-accent-tint-hover` is adopted or deleted (§6.6). Both are acceptable;
  what is not acceptable is a third dossier leaving it dead.

## 15. Deviation Log

Appended by /build. AC-8's measured sizes, AC-9's contrast table and AC-3/AC-5/AC-6's
per-surface manual results are recorded here.

## 16. Follow-ups

- **FU-1** - phase 1's FU-1: the three undocumented one-off sizes (11px in `LocaleToggle` and
  `AppSidebar.vue:289`, `0.7rem` in `STabs`, `0.9375rem` in `SIdleDialog`) still carry
  literal values. They should be snapped to the ramp as part of this dossier's typographic
  work if the type scale settles, or carried forward with a decision.
- **FU-2** - the page width policy remains undecided (`2026-08-19-content-area-spacing-and-scroll-contract`
  Q-15/Q-16/FU-4): six views cap their own width and the rest do not, with no intent source.
  It is a layout question, excluded by this dossier's non-goals, and the answer belongs in
  `docs/UI/12-shared-patterns.md`.
- **FU-3** - `SBadge.vue:120-141`'s contradictory touch-target declarations
  (`min-width: 44px` immediately followed by `min-width: unset`, with a `::before` supplying
  the real target) and `SInput.vue:236-244`'s equivalent. Dead declarations, not this
  dossier's property set. Route to `check-quality`.
- **FU-4** - `AppShell.vue:177-181` still hard-codes `300ms` to match `--transition-slow`
  (`2026-07-05-sitewide-ui-enhancement`'s FU-10). `--duration-slow` now exists and would fix
  it in one line.
- **FU-5** - the product has no display face and no logotype; the wordmark is
  `AppTopBar.vue:42-47`'s text "SMAP" in the accent colour. A distinctive identity needs
  both, and both are out of scope here (§5). Worth its own dossier if distinctiveness rather
  than quality is the goal.
- **FU-6** - `WorkflowNodeComponent.vue`'s 14 hard-coded colours and
  `GraphragGraphView.vue`'s 8 are categorical palettes retained by Q-12. Neither has been
  checked for colour-blind safety or for contrast against its own canvas, in either theme.
