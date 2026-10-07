---
type: bugfix
status: implemented
created: 2026-10-07
requirements: [R6.14, R6.15, R13.34]
depends_on: []
---

# Normalise Google profile names before they become account display names

## 1. Summary

Signing in with Google stores the Google profile's `name` claim as the account display name
without the normalisation every other display-name write applies. A name longer than 50
characters does not fit `users.display_name` (`VARCHAR(50)`), the database refuses it, and the
error escapes every handler: the Google callback answers 500 instead of signing the user in.
For an existing account that has no display name yet, the failure repeats on every retry, so
that person can never sign in with Google. Names that fit are stored raw, including control
and format characters the shared label rule exists to strip. This dossier fixes findings F-1,
F-6 and F-17 of `docs/audits/2026-10-07-display-name-resolution/findings.md`: Google names go
through the same normalisation as profile updates, truncation stops splitting characters a
reader sees as one (grapheme clusters), and existing account names are repaired once.

## 2. Observed vs Expected

- **Observed** — `GoogleOidcClient` returns the `name` claim untouched
  (`backend/contexts/identity/infrastructure/oauth/google.py:231`). `AuthService` writes it
  raw on both branches that set a name: the existing-user branch
  (`backend/contexts/identity/application/auth_service.py:522-523`, outside any savepoint)
  and the new-user branch (`:532-540`, inside `begin_nested` but catching only
  `IntegrityError` at `:547`). `users.display_name` is `VARCHAR(50)`
  (`backend/contexts/identity/infrastructure/tables.py:24`,
  `backend/alembic/versions/0033_user_display_name.py:26`). A value over 50 characters makes
  Postgres raise SQLSTATE 22001; asyncpg raises `StringDataRightTruncationError`, a
  `DataError`, which SQLAlchemy surfaces as a `DBAPIError` rather than an `IntegrityError`.
  `complete_google_callback` catches only `IdentityError` (`auth_service.py:713`) and the
  route has no handler (`backend/app/api/v1/auth.py:679`), so the request ends in the generic
  500 (`backend/shared_kernel/errors/handlers.py:171`) after the one-time OAuth state was
  consumed. On the existing-user branch the identity row inserted at `auth_service.py:505`
  rolls back with the request, so the next attempt takes the same branch and fails the same
  way. Separately, `normalise_label` truncates by code point
  (`backend/shared_kernel/labels.py:61`), so a cut can land inside a grapheme cluster and
  leave a dangling zero-width joiner or a single regional indicator.
- **Expected** — a Google sign-in succeeds and issues the same session artifacts as a
  password login ([R6.14], [R6.15]) whatever the profile name is. Account display names are
  normalised at the source ([R13.34]), as `AuthService.update_profile` already does through
  `_normalise_display_name` (`auth_service.py:989, 1154-1163`) and admin provisioning does at
  `admin_service.py:187`. Normalisation keeps printable Unicode including emoji
  (`labels.py:29-39, 52-53`), which a truncation that splits a grapheme cluster contradicts.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What happens to a Google name longer than 50 characters? | Normalise and truncate to 50 at a grapheme boundary. | Requester's choice. Keeps most of the name, matches the 50-character cap profile update and provisioning already enforce, and the user can edit it afterwards. Storing nothing was rejected because it discards a usable name; widening the column was rejected because it would leave Google names on a different cap from every other path. |
| Q-2 | How does truncation avoid splitting a grapheme cluster? | Use the `regex` package's extended grapheme clusters (`\X`), declared as an explicit backend dependency. | Requester's choice. `regex` is already installed as a dependency of `tiktoken` (`pip show regex` reports "Required-by: tiktoken"), so the fix adds a pin, not new runtime code, and gets Unicode's segmentation rules rather than an approximation. A stdlib heuristic was rejected as incomplete. |
| Q-3 | Repair names already stored raw? | One data migration runs the normalisation over every `users.display_name`. | Requester's choice. Rows written by Google sign-in cannot be told apart from others, so the repair covers all accounts; it is idempotent for rows that are already clean, and it also clears rows that predate the source guard, which [R13.34] says may exist. A name that normalises to nothing becomes `NULL`, the state profile update gives a cleared name. |
| Q-4 | Does this depend on another dossier? | `depends_on: []`. | No dossier that is not implemented touches `auth_service.py` or `shared_kernel/labels.py`. The later `2026-10-07-display-name-validation` dossier from the same audit also edits `normalise_label` and will list this one in its `depends_on`. |

## 4. Reproduction

1. Configure Google OIDC (or, in a unit test, stub `AuthService._verify_google`).
2. Sign in with a Google account whose profile name has 51 or more characters, for example
   "Professor Dr. Maria-Fernanda de los Santos Villanueva" (53 characters), and an email no
   SMAP account uses yet. The callback answers 500 instead of redirecting to the SPA.
3. Create a password account for an email, without a display name, then sign in with a
   Google account with that email and a long profile name. Every attempt answers 500, and
   the account never gains a Google identity link.
4. Sign in with a Google account whose name contains U+202E (right-to-left override). The
   name is stored with the override and renders reversed on every room surface.
5. In a Python shell, `normalise_label("abc" + "\U0001F468‍\U0001F469‍\U0001F467", max_len=5)`
   returns a string ending in a zero-width joiner.

## 5. Root Cause Analysis

1. The `name` claim is passed through as is (`google.py:231`).
2. `AuthService` is the application layer that owns the account-name rule (it already wraps
   `normalise_label` in `_normalise_display_name`, `auth_service.py:1154-1163`), but its two
   Google writes call the repository with `profile.name` directly (`auth_service.py:523, 539`).
   **Root cause:** the Google branch of `AuthService` writes the display name without
   `_normalise_display_name`, so neither the 50-character cap nor the character rule applies.
3. The over-long value reaches the column and the database rejects it with a `DataError`.
4. Aggravating: both branches catch only `IntegrityError` (`auth_service.py:511, 547`), and
   the existing-user write runs outside any savepoint, so the error aborts the request; the
   rollback also undoes the identity link, which makes the failure repeat for that account.
5. Separate root cause for F-17: `normalise_label` slices code points
   (`labels.py:61`) instead of whole grapheme clusters. It is barely reachable today because
   every API field's `max_length` equals the cap (`guests.py:40,73,179`, `auth.py:225`,
   `admin_users.py:61`), and becomes reachable through the fix above, which truncates
   Google names.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every Google sign-up with a profile name over 50 characters; every
  existing account without a display name that signs in with or links Google with such a
  name (locked out of Google sign-in until the name changes on Google's side or the user
  sets a display name some other way); every Google-sourced name carrying control or format
  characters, shown on every room surface. No bad over-length data was persisted (the write
  fails); raw short names were.
- **Sibling suspects**:
  - Other writers of `users.display_name`: profile update (`auth_service.py:989`) and admin
    provisioning (`admin_service.py:187`) normalise — cleared. Password registration writes
    no name (`app/api/v1/auth.py:145-148`) — cleared. Linking Google from the profile
    ([R6.17]) writes no name (no other `set_display_name` caller,
    `contexts/identity` grep) — cleared.
  - Other length-capped columns fed by user input: `chatroom_guests.display_name`,
    `guest_sessions.display_name` and `chatroom_guest_bans.display_name` (`VARCHAR(100)`,
    `contexts/conversation/infrastructure/tables.py:182, 213, 234`) are written only through
    `normalise_label(max_len=100)` or from an already-normalised session — cleared. Canvas
    template `name` and snapshot `label` (`VARCHAR(200)`,
    `contexts/canvas/infrastructure/tables.py:132, 164`) are capped by Pydantic
    `max_length=200` (`app/api/v1/canvas_templates.py:56, 106`; `app/api/v1/canvas.py:154`)
    — cleared.
  - Other external identity claims written to columns: Google `email` and `sub` go to `Text`
    columns (`identity/infrastructure/tables.py:23, 89, 92`) — cleared.
  - Other callers of `normalise_label` (guest session names, room guest labels, profile
    update, provisioning) share the code-point truncation of F-17 and are fixed by the same
    change.

## 7. Fix Design

1. **Truncation by grapheme cluster** in `shared_kernel/labels.py`: after stripping, keep
   whole extended grapheme clusters (`regex.findall(r"\X", cleaned)`) while their combined
   code-point length stays within `max_len`, then trim. The cap stays a code-point count, so
   the result always fits the `VARCHAR(n)` columns, which count characters. Declare `regex`
   in `backend/pyproject.toml` dependencies at the version already installed.
2. **Normalise Google names at the source**: in `AuthService`, compute
   `name = _normalise_display_name(profile.name)` once per resolution and use it for both
   the existing-user branch (`auth_service.py:522-523`, set only when the account has no
   name and `name` is not `None`) and the new-user branch (`:539`). The infrastructure
   client stays a transport for the claim; the rule lives where the account-name rule
   already lives.
3. **Data repair**: migration `0100_normalise_account_display_names` reads every non-null
   `users.display_name`, applies the normalisation and updates the rows whose value
   changes. The migration carries its own copy of the rule as of this revision, so a later
   change to `labels.py` cannot alter what an already-applied migration did. The downgrade
   is a no-op, because the stripped characters are not recoverable and were never valid
   input.

The fix corrects the write path rather than catching the database error: catching
`DataError` would turn the 500 into some other refusal while the name stayed unusable.

## 8. Regression Test Plan

Written first; each fails against current code for the stated reason.

- `backend/tests/unit/test_auth_service_oauth.py`: a new-user provisioning with a 60-character
  Google name asserts the repository insert receives at most 50 characters; it fails today
  because the raw 60-character name is passed. A second case with a name containing U+202E
  and a newline asserts both are stripped; it fails today for the same reason. A third case
  on the existing-user branch (account without a display name, verified email) asserts
  `set_display_name` receives the normalised name.
- `backend/tests/unit/test_label_normalisation.py`: truncating
  `"abc" + family emoji (ZWJ sequence)` at a cap that falls inside the emoji drops the whole
  emoji rather than leaving a trailing ZWJ; a flag (two regional indicators) cut after the
  first indicator drops the flag; a base letter with a combining accent is never separated.
  These fail today because the slice is by code point.
- `backend/tests/integration/test_migration_0100_display_names.py` (db tier, scratch database
  as in `test_migration_0087_schema.py`): seed rows with a bidi override, a newline, a
  control-only name and a clean name; after upgrade the first two are normalised, the third
  is `NULL`, the fourth is unchanged; running the upgrade logic twice changes nothing.

## 9. Risks and Rollback

- A user whose Google name is truncated sees a shorter name than Google shows; profile
  update lets them change it.
- The backfill changes stored names: a name that relied on a stripped character (for
  example a deliberate right-to-left override) renders differently afterwards. This is the
  intended effect of [R13.34].
- Declaring `regex` makes an already-installed transitive dependency direct; the supply
  chain audit treats it as a new direct pin.
- Rollback: reverting the code restores the old write path; the migration's downgrade is a
  no-op and repaired names stay repaired, which old code reads without issue.

## 10. Acceptance Criteria

- [x] AC-1: the regression tests in §8 fail before the fix and pass after.
- [x] AC-2: a Google sign-in whose profile name exceeds 50 characters succeeds for a new
  account and for an existing account without a display name, storing a name of at most 50
  characters that ends on a grapheme boundary.
- [x] AC-3: a Google profile name is stored with exactly the result `_normalise_display_name`
  gives for it, so control and format characters other than ZWJ and VS16 never reach
  `users.display_name` through Google sign-in.
- [x] AC-4: `normalise_label` never returns a string that ends inside a grapheme cluster, for
  every caller.
- [x] AC-5: after migration `0100`, every non-null `users.display_name` equals its own
  normalisation, and the migration upgrades and downgrades cleanly on the scratch database.
- [x] AC-6: backend lint, typecheck, unit and db-tier tests and the dependency audit pass in
  CI.

## 11. SRS Delta

None. [R13.34] already requires account display names to be normalised at the source; the
fix restores it.

## 12. Deviation Log

- **D-1. Q-2's premise was wrong: `regex` is a new runtime dependency, not a new pin.**
  `pip show regex` reported "Required-by: tiktoken" on the developer machine, but `tiktoken`
  there comes from unrelated locally installed tools; neither appears in
  `backend/requirements.lock`, which is all the runtime image installs. Found at the
  freshness check; the requester chose to keep `regex` anyway. It is declared as
  `regex>=2026.1.15,<2027` in `pyproject.toml` (the upper bound from the `check-security`
  hardening note) and pinned `==2026.1.15` in `requirements.lock`, and `types-regex` is
  pinned exactly in the dev extras, following the repo's rule for typeshed stubs. `pip-audit`
  reports no known vulnerabilities for either. §9's third risk bullet understates this.
- **D-2. Migration `0100` revises `0102`.** The number was claimed by this dossier before
  `0102` landed, so the chain runs `0099 → 0102 → 0100`, as BOARD.md warned.
- **D-3. One extra regression test on each side.** A Google name that normalises to nothing
  does not overwrite an existing account's missing name (`set_display_name` is not called),
  and the migration test also pins its frozen copy of the rule against `normalise_label` so
  the two cannot drift silently.

## 13. Follow-ups

- **FU-1.** The existing-user branch writes the display name outside a savepoint
  (`auth_service.py:522-523`); with the name normalised it cannot fail on length any more,
  but any future write there that can fail will again roll back the identity link with it.
- **FU-2.** The guest name form validates length with `zod`'s `max(100)`, which counts UTF-16
  code units, while the server caps code points; a name full of emoji is rejected by the
  form earlier than the server would cut it. Harmless, but the two counts differ.
- **FU-3.** A single grapheme cluster longer than the cap (a letter carrying dozens of
  combining marks) truncates to nothing, so the name becomes `NULL` rather than a cut
  cluster. Deliberate, but no message tells the user why their name was not taken.
- **FU-4.** No live Google sign-in with a long name has been observed in a running stack;
  AC-2 rests on unit tests with `_verify_google` stubbed. A staging check.
