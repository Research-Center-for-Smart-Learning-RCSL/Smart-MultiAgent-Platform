# Frontend Motion Language

Authoritative spec for interface motion in the SMAP frontend, per [R24.49].
The short version lives as a comment block in `frontend/src/shared/styles/main.css`
next to the motion tokens; this document carries the reasoning and the patterns.

## Principles

Restrained professional. SMAP is a tool people use for hours a day: motion exists
to communicate causality (what appeared, where it came from, what responded), never
to entertain. If a user would notice an animation on their 50th visit, it is too much.

Every rule below collapses automatically under `prefers-reduced-motion: reduce` via
the global freeze in `main.css` (`@layer base`); JS-driven motion must additionally
gate itself with `usePrefersReducedMotion()`.

## Tokens

Defined in the `@theme` block of `main.css`:

| Token | Value | Use |
|---|---|---|
| `--transition-fast` | 150ms ease | hovers, small state flips |
| `--transition-normal` | 200ms ease | elevation, color, lifts |
| `--transition-slow` | 300ms ease | layout (sidebar collapse) |
| `--motion-rise` | 6px | entrance translate distance |
| `--motion-lift` | 2px | hover lift distance |
| `--ease-out-soft` | cubic-bezier(0.25, 0.8, 0.4, 1) | entrances |
| `--ease-spring` | cubic-bezier(0.34, 1.3, 0.5, 1) | playful one-shots only, never hover |

Note: `--transition-*` are duration+easing shorthands — never append another
easing after them in a `transition` declaration.

## Patterns

- **Route change** — `<Transition name="route" mode="out-in">` in `App.vue`:
  outgoing view fades 120ms, incoming fades + rises 6px over 180ms. Views keyed
  by `$route.path`, so query-only changes never remount or re-animate.
- **Hover lift** — interactive cards use `SCard hoverable`: translateY(-2px) +
  one elevation step (`--elevation-2`) over `--transition-normal`. Do not
  hand-roll lifts; use the prop.
- **List entrance** — first load only: `useListStagger(loadingRef)` from
  `@shared/composables` returns a class to bind on the STable/list container;
  the CSS (`.list-stagger` in `main.css`) rises each row 4px over 200ms with a
  30ms/row delay capped at 270ms. Refetches, pagination, and live updates render
  statically by design.
- **Overlays** — SModal/SDrawer/SDropdown ship their own enter/leave
  transitions; reuse the components, never reimplement.
- **Shell** — sidebar collapse tweens the grid track (`--transition-slow`);
  the active nav indicator grows in with `--transition-fast`; the topbar gains
  `--elevation-2` once content is scrolled.

## When not to animate

- Layout-critical positions (tables reordering, form fields appearing on error).
- Text content changes; skeletons cover loading, not transitions.
- Anything periodic or looping in the app shell (decorative loops belong to the
  marketing landing page only, where they pause offscreen).
- Focus movement — focus must land instantly.

## Adding motion to a new view

1. Reach for an existing pattern above first (usually the answer).
2. If a new animation is genuinely needed: duration from the token scale,
   entrance easing `--ease-out-soft`, distance ≤ `--motion-rise`, and verify it
   collapses cleanly under reduced motion (the global freeze snaps keyframe
   animations to their final frame — design end states accordingly).
3. Keyframes whose resting state is the final frame (see `ac-fill` in
   `AgentConstellation.vue` for the canonical example).
