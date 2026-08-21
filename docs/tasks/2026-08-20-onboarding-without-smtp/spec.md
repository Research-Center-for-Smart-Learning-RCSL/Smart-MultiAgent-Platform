---
type: feature
status: implemented
created: 2026-08-20
requirements: [R6.01, R6.02, R6.05, R6.09, R6.10, R19a.12, R19a.13]
depends_on: []
---

# Onboarding Without SMTP

## 1. Summary

A self-hosted SMAP deployment with no outbound mail can register users (CAPTCHA has an
`off` mode) but cannot reliably get them *into* an Org or Project: the only route to a
membership row is an emailed invite. This dossier closes that gap without weakening the
anti-enumeration and consent properties the identity context already has, by adding three
things: every invite returns a copyable accept link, the project invite form picks from a
pool the inviter can already see instead of asking for a typed address, and an Admin can
provision an account and hand over two copyable activation links.

**A correction that shapes the whole scope.** SMTP is *not* required for an invitee who
already has an account. `InviteService._notify_invitee` (`invite_service.py:159-183`)
looks the address up in `users` and writes an in-app notification, and
`InviteRepository.list_for_user` (`repositories.py:595-619`) matches invites by
case-insensitive email, so the invite already appears in `/invites`
(`InboxInvitesView.vue`) and can be accepted there. The real gaps are narrower than they
look: an invitee with **no account yet**, and the friction of typing an address the
inviter cannot verify.

## 2. Goals and Non-goals

**Goals**

- An Org or Project Owner can complete an invite end to end with no working mail server.
- A Project Owner invites by picking from a list rather than typing an address, without
  that list disclosing anything they could not already see.
- An Admin can provision an account for someone who cannot self-register, and hand them
  what they need to log in.
- No new way to learn whether an email address has an account.
- Consent is preserved: nobody is placed in an Org or a Project without accepting.

**Non-goals**

- **Adding members without their consent.** Decided in Q-1: both Org and Project
  membership still require the invitee to accept. No endpoint writes an `org_members` or
  `project_members` row on someone else's behalf.
- **An invite-only registration mode.** Q-4: the existing admin-tunable email-domain
  allowlist (R19a.13, `email_domain_policy.py`) already closes a deployment to one
  institution's addresses, and CAPTCHA has `mode=off` for offline installs
  (`shared_kernel/auth/captcha.py:79-92`). §9 records the operator recipe; no new config
  surface is added.
- **Relaxing R6.02.** Q-3: an Admin-provisioned account is still created unverified and
  must verify before it can accept an invite or create an Org/Project.
- **A forced first-login password change.** Not needed once the password is set by the
  account holder through a token link rather than issued to them (Q-2).
- **Bulk import.** One account and one invite at a time. §16 FU-1.
- **Group membership.** Member Groups are `2026-08-20-member-groups-and-room-visibility-isolation`.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should "add an existing user directly" bypass their consent? | No, at both levels. Only the UI improves: pick from a list, and the invite still has to be accepted. | Writing a membership row for someone else is a unilateral act with real consequences here — an Org Owner is a Project Owner on every project of the Org (R8.08), so being added to an Org exposes the user's project activity to whoever added them. |
| Q-2 | How does an Admin-created account get a password? | A copyable set-password link, reusing `password_reset_tokens` (R6.05, 30 min, single use). | No plaintext password crosses an HTTP response, no SMTP needed, and the token mechanism already exists with expiry and single-use semantics. The 30-minute TTL is handled by a re-issue endpoint rather than by changing R6.05 (§6). |
| Q-3 | Is an Admin-created account marked `email_verified`? | No. It is created unverified, and the Admin is given a second, separate copyable verification link. | Keeps R6.02 and the two token mechanisms separate. **Stated plainly: handing the verification link over out of band proves nothing about address ownership**, so the guarantee is "an Admin vouched", not "the address was proven" — the separation buys audit clarity and an unchanged SRS, at the cost of one extra step. |
| Q-4 | Add a `registration_mode=open\|invite_only` switch? | No. The domain allowlist is sufficient. | R19a.13 is already admin-tunable at runtime and already denies every address outside the institution. A second gate with its own states would be tested and maintained for no additional closure. |
| Q-5 | Should the invite response tell the owner "this address already has an account"? | No. The accept link is returned unconditionally instead. | It would be an account-existence oracle: `register` deliberately returns a uniform 202 for both branches (SEC-M4, `auth_service.py:167-195`), and any verified user can create an Org and become an Owner, so capability #14 is not a meaningful bound. Returning the link in both cases removes the owner's *need* to know. |
| Q-6 | Which pool does the "pick a user" control read? | Project invites under an Org read the parent Org's member list. Org invites, and invites to a user-owned project, keep the typed-address field. | `GET /api/orgs/{id}/members` (`orgs.py:278`) is already readable by any member of that Org, so the picker discloses nothing new. There is no pre-existing pool for an Org invite, so a picker there would have to read the platform user directory — a new disclosure this dossier refuses. |
| Q-7 | Overlap with `2026-08-20-member-groups-and-room-visibility-isolation`? | Not a `depends_on`; whoever builds second rebases. | Both touch `ProjectMembersView.vue` (this one rewrites the invite form, that one adds a link to the groups view), `projects.py` (this one at `:346`, that one at `:86-117`) and the tenancy locale files. Different regions in each case. This is the same call the user made for the same shape of overlap in that dossier's Q-7. |

## 4. Current State

### 4.1 The only path to a membership row

`org_members` and `project_members` rows are written in exactly one place:
`InviteService._finalize_acceptance` (`contexts/tenancy/application/invite_service.py:383-398`).
Invite creation is `orgs.py:357-383` and `projects.py:346`, both gated on capability #14.
`InviteOut` (`orgs.py:375-383`) returns id, scope, role, email, state and expiry — **not**
the plaintext token, which `InviteCreated.plaintext_token` (`invite_service.py:55-58`)
carries out of the service and the route then drops.

The token is hashed into `invites.token_hash` (`tenancy/infrastructure/tables.py:99`) and
mailed as a URL fragment (`email_templates.invite_accept_url:22-23`,
`{origin}/?invite=1#token=`), which `Landing.vue:46-48` forwards to
`/invites/accept` (`slices/tenancy/routes.ts:52-60`). Acceptance requires a logged-in,
**verified** caller on both paths (`app/api/v1/invites.py:96-99`, `:129-132`).

### 4.2 What already works without mail

- **Existing invitee.** `_notify_invitee` (`invite_service.py:159-183`) writes a
  `NotificationKind.INVITE_RECEIVED` row when the address matches a live user, and the
  invite is listed at `/invites` by case-insensitive email match
  (`repositories.py:604-607`). No mail involved.
- **Self-registration offline.** `captcha.verify` returns immediately in `mode=off`
  (`shared_kernel/auth/captcha.py:95-105`), and the domain allow/deny policy
  (`email_domain_policy.py`, R19a.13) restricts who may register at all.
- **Rate limiting.** `_email_invite` already caps per recipient at 5 per 10 minutes and
  skips the mail rather than failing the invite (`invite_service.py:207-214`).

### 4.3 What does not work without mail

- **Unregistered invitee.** They need the token, which only the mail carries. The invite
  row exists and is inert.
- **Admin provisioning.** `admin_users.py` has list, get, ban, unban, soft-delete,
  hard-delete, and admin promote/demote. There is no create. An operator who must onboard
  someone unable to self-register has no route at all.
- **The verified gate.** An account that cannot receive `send_email_verification`
  (`auth_email_service.py:64-73`, `{origin}/verify-email#token=`) can never verify, and an
  unverified account cannot log in (`auth_service.py:328-329`) or accept any invite
  (§4.1). This is why Q-3's answer requires a second copyable link rather than just a
  password one.

### 4.4 Token and URL mechanics to reuse

| Purpose | Store | TTL | Link built at |
|---|---|---|---|
| Email verification | `email_verify_tokens` (`identity/infrastructure/tables.py:68-79`) | 24 h (`auth_service.py:67`) | `auth_email_service.py:70` |
| Password reset | `password_reset_tokens` (`:55-66`) | 30 min, R6.05 (`auth_service.py:68`) | `auth_email_service.py:101` |
| Invite accept | `invites.token_hash` | per-invite `expires_at` | `email_templates.py:22-23` |

All three ride in the URL **fragment** so the token never reaches a server log, a
`Referer`, or browser history (SEC-8, `auth_email_service.py:65-70`). Returning them in a
response body is a different exposure and is addressed in §8.

## 5. Design

### Options considered

**Option A — direct membership write** (`POST /api/projects/{id}/members` with an exact
email). Fewest steps. Rejected by Q-1 on consent, and it would have been an
account-existence oracle by construction: "added" and "no such user" are different
outcomes no amount of response shaping hides.

**Option B — always return the accept link, improve the picker, add admin provisioning
(chosen).** Nothing about the consent model or the invite state machine changes. The only
new membership-adjacent surface is the Admin one, which creates a `users` row and no
membership at all.

**Option C — make the invite email optional per deployment.** A config flag that skips
mail entirely. Rejected: it solves nothing Option B does not, and it removes mail for
deployments that have it.

### Decision

Option B, in three independent pieces that can ship in any order.

**1. `InviteOut` gains `accept_url`, populated only on the 201 from create.** The read
endpoints (`GET /api/invites`, and any future invite listing) never carry it: the token
is single-purpose and the inviter is the only party who needs a copy. Consequence: if the
owner loses the link, the correct recovery is to revoke and re-invite, not to re-read.
Recorded as an accepted limitation rather than hidden — §16 FU-2 carries "re-issue" if it
proves annoying in practice.

**2. The project invite form becomes a picker over the parent Org's members (Q-6),** with
the typed-address field retained as an alternative for someone not yet in the Org. Org
invites and invites to user-owned projects keep the typed field only. The picker excludes
users who already hold a `project_members` row and users with a live pending invite for
that scope, so the two error paths the current form can hit
(`/tenancy/invite-duplicate`, handled at `ProjectMembersView.vue:103-105`) become
unreachable from the picker.

**3. `POST /api/admin/users` creates an unverified, password-less account** and returns
two copyable links built exactly as the mailers build them (§4.4). Because
`password_reset_tokens` lives for 30 minutes, `POST /api/admin/users/{id}/activation-links`
re-mints both on demand: the Admin clicks it when they are actually with the person,
rather than racing a timer. R6.05's 30 minutes is untouched.

**Not built, deliberately:** no forced first-login password change, because the password
is never known to anyone but the account holder; no `must_change_password` column; no
plaintext password in any response.

## 6. Detailed Changes

### Backend

**`contexts/tenancy`**

- `application/invite_service.py` — `create_org_invite` / `create_project_invite` already
  return `InviteCreated` with the plaintext token; add a helper that renders it through
  `email_templates.invite_accept_url` so the route does not rebuild the URL shape. Public
  origin resolution already exists at `_default_public_origin` (`:61-64`).
- `application/invite_service.py` — `invitable_org_members(project_id)`: parent-Org
  members minus existing project members minus live pending project invites. Reads only
  tenancy tables.
- `interfaces/facade.py` — expose the above for the route.

**API contract** (`gen:api` rerun required)

- `InviteOut` gains `accept_url: str | None`. Populated on `POST /api/orgs/{id}/invites`
  and `POST /api/projects/{id}/invites`; `None` everywhere else.
- `GET /api/projects/{project_id}/invitable-members` — capability #14, returns the Q-6
  pool. 200 with an empty list for a user-owned project (no parent Org), never a 404: an
  empty pool is a state, not an error.
- `POST /api/admin/users` — admin only. Body: `email`, optional `display_name`. Creates
  `users` row with `status=pending`, `email_verified=false`, `password_hash=NULL` (the
  column has been nullable since 0063). Returns the user plus
  `{set_password_url, verify_email_url}`. Rejects an address that already has a live
  account with a 409 — this endpoint is admin-only and admins already have
  `USER_READ_ANY` (capability #24), so it is not a new oracle.
- `POST /api/admin/users/{user_id}/activation-links` — admin only, re-mints both tokens
  and returns the same pair.
- No change to `POST /api/auth/register`, `/verify-email`, `/password-reset/*`.

**`contexts/identity`**

- `application/admin_service.py` — `create_user(email, display_name, actor_admin_id)` and
  `issue_activation_links(user_id)`. Password validation is not involved (no password is
  set); email normalisation reuses `_normalise_email`, and the domain policy
  (`email_domain_policy.is_allowed`) **is** applied, so an Admin cannot provision an
  address the deployment's own policy forbids.
- Audit: `user.created` with `metadata={"provisioned_by_admin": true}` reusing the
  existing action name from `auth_service.py:208`, and a new
  `admin.user_activation_links_issued` on every mint (including the create), carrying the
  recipient digest and never the token.

### Frontend

- **tenancy slice** — `ProjectMembersView.vue`: the invite card becomes a picker
  (`SSelect` over the invitable pool) with a "not in the organization yet" escape hatch to
  the current email field; on success, render the returned `accept_url` in a copyable
  field with a copy button and a one-line explanation that the invitee also sees it in
  their in-app inbox. `OrgMembersView.vue`: keep the email field, add the same copyable
  result. `api/invites.ts`, `api/orgs.ts`, `api/projects.ts`, `queries/index.ts`,
  `locales/{en,zh-TW}.json`.
- **admin slice** — `AdminUsersView.vue` gains a "Create user" action and a result dialog
  showing both links with copy buttons and their expiries; `AdminUserDetailView.vue` gains
  a "Re-issue activation links" action. Both links must be visually labelled with what
  they do, because handing over the wrong one is the obvious failure mode.
- No new routes: everything lands in existing views, so `app/router.ts` is untouched.
- Copy-to-clipboard: check for an existing helper in `@shared/composables` before adding
  one; if none exists, add it there rather than in a slice.

### Deploy/config

None. §9 documents the existing operator recipe (`captcha mode=off` + domain allowlist)
in `docs/operations.md`.

## 7. NFR Checklist

- **i18n** — new strings in both locale files. The invite result text will contain an
  email address; bind it, never interpolate a literal `@` into a message string (vue-i18n
  reads `@` as a linked message and only fails in a production build).
- **Audit log** — `user.created` (with the admin provenance flag),
  `admin.user_activation_links_issued`, and the existing `org.member_invited` /
  `project.member_invited`. No token, no plaintext address: the codebase's
  `recipient_digest` is the established form (`invite_service.py:67-69`).
- **Tenant isolation** — `invitable-members` resolves the parent Org from the project row
  and refuses a project the caller cannot manage; it never accepts an org id from the
  client.
- **Error handling UX** — copy buttons need a success state; an expired activation link
  must say so and point at the re-issue action rather than failing opaquely.
- **Performance** — the invitable pool is one join over `org_members` minus two anti-joins;
  bound it with the existing `PaginationParams` and a search filter if an Org is large.

## 8. Security Considerations

- **The accept link is a bearer token in a response body.** `accept_by_token`
  (`invite_service.py:321-345`) treats possession as authorisation, by design. Returning
  it to the inviter is safe (they created it), but it must not be logged, audited,
  included in an error body, or returned by any read endpoint. The same rule applies to
  the two admin activation links, which are stronger: the set-password link grants control
  of an account.
- **No new account-existence oracle** (Q-5). The invite response is byte-identical whether
  or not the address has an account. The Admin create endpoint's 409 is not an oracle
  because capability #24 already lets an admin list and search users
  (`admin_users.py:76-99`).
- **The picker discloses nothing new** (Q-6). Its pool is a subset of
  `GET /api/orgs/{id}/members`, already readable by any member of that Org.
  **This bullet, and Q-6's rationale, are wrong as written — see D-12.** Capability #14
  does not imply membership of the parent Org, so the subset claim held only for callers
  who happened to be Org members. Left in place as the approved record; the implementation
  makes the claim true by scoping the query to the caller's own org membership.
- **Domain policy applies to admin provisioning too.** Skipping it would make the Admin
  endpoint a bypass of a control the operator deliberately set.
- **Rate limit the link mints.** `activation-links` re-mints a credential; cap it per
  target user (reuse `ratelimit.check_raw`, as `_email_invite:209` and
  `request_password_reset:841-844` do) so a compromised admin session cannot grind tokens.
- **Consent is a security property here** (Q-1), not a courtesy: an Org Owner is a Project
  Owner on every project of the Org (R8.08), so a unilateral Org add would hand someone
  read access to the added user's project work.
- **Unchanged gates.** Login still requires `email_verified` (`auth_service.py:328-329`)
  and invite acceptance still requires it (`invites.py:96-99`). Nothing here weakens R6.02.

## 9. Quality Notes

**Existing debt in touched files** (record, decide explicitly, do not imitate):

- `invites.py:96-99` and `:129-132` cite **R6.11** for the verified-email gate, but R6.11
  in `REQUIREMENTS.md:263` is about Guest links. The gate's actual source is R6.02
  (`REQUIREMENTS.md:219`), which `permissions.py:311` cites correctly. Fixing these two
  comments is in scope (AC-11) because this dossier's Q-3 turns on that gate.
- Both accept routes do a function-local `from shared_kernel.auth.dependencies import
  _raise_forbidden` and call a private helper. Leave it; a public forbidden-raiser is a
  separate cleanup. FU-3.
- `invite_service.py` reaches `users` with raw SQL to avoid a cross-context repository
  import (`:167-174`), with a comment saying the choice is intentional. Follow that
  precedent rather than "fixing" it.

**Patterns to follow:**

- Anti-enumeration: `auth_service.register:167-195` — uniform outcome, rate-limited side
  effect, audit on the branch the caller cannot see.
- Token issue/consume: `self._verify.issue` / `self._reset.consume`
  (`auth_service.py:202`, `:875`).
- Admin-only route shape: `admin_users.py` `Depends(require_admin)` plus `RequestContext`
  for `actor_ip`.
- Frontend invite form and member table: `ProjectMembersView.vue` +
  `composables/useMemberActions.ts`.

**Reuse inventory:** `email_templates.invite_accept_url`, `auth_email_service`'s link
shapes, `IdentityFacade.recipient_digest`, `shared_kernel.auth.ratelimit.check_raw`,
`_normalise_email`, `email_domain_policy.is_allowed`, `PaginationParams`, `@shared/ui`
`SSelect` / `SInput` / `SFormField` / `SButton`, `useToast`, `useConfirmDialog`.

**Operator documentation (AC-12).** `docs/operations.md` gains a short "closed deployment"
recipe: set CAPTCHA `mode=off`, set the email-domain allowlist to the institution's
domains, and use the copyable links. This is the substitute for the invite-only switch
Q-4 declined, and without it the decision is undiscoverable.

## 10. Risks and Rollback

| Risk | Mitigation |
|---|---|
| A copied activation link is pasted into a group chat and grants account control. | Short TTLs (30 min / 24 h), single-use tokens, and UI copy that says the set-password link is equivalent to a password. Re-issue is one click, so an operator has no incentive to keep an old link around. |
| The accept link appears in a screenshot or a support ticket. | Same single-use property; revoking the invite invalidates it. The UI labels it as sensitive. |
| An admin provisions an address the person does not control. | The audit trail names the admin (`user.created` provenance flag). This is the residual cost of Q-3's out-of-band verification, stated in Q-3 rather than mitigated away. |
| Rollback. | No migration. Every change is additive: an unset `accept_url`, an unused endpoint, and an unused admin action. Reverting the frontend alone leaves a working system. |

## 11. Acceptance Criteria

- [x] AC-1: `POST /api/orgs/{id}/invites` and `POST /api/projects/{id}/invites` return an
      `accept_url` that redeems successfully through `POST /api/invites/accept-by-token`.
      (`test_onboarding_invite_links.py`; redeemed for real against Postgres in
      `test_onboarding_without_smtp_db.py`.)
- [x] AC-2: The response is identical in shape and content whether or not the invitee's
      address has an account — a test asserts both branches, including that no field, no
      status code and no header differs. (Byte-compared over `TestClient` on both routes,
      plus a service-level test pinning that the *only* difference is the in-app
      notification.)
- [x] AC-3: `accept_url` is absent from `GET /api/invites` and from every other invite
      read path. (`invites.InviteOut` has no such field; both create models default it to
      `None`.)
- [x] AC-4: `GET /api/projects/{id}/invitable-members` returns parent-Org members who are
      neither project members nor holders of a live pending invite; returns 200 with an
      empty list for a user-owned project; refuses a caller without capability #14.
      (The two anti-joins and the path-derived project id are asserted on the compiled SQL;
      the 403 runs the real §5.2 matrix. The pool is additionally empty for a caller who is
      not a member of the parent Org — D-12 — proven against real rows.)
- [x] AC-5: `POST /api/admin/users` creates a `pending`, unverified, password-less account,
      returns both links, and is refused for a non-admin.
- [x] AC-6: An address the deployment's email-domain policy denies is refused by
      `POST /api/admin/users`.
- [x] AC-7: Walking the returned links in order — set password, then verify — produces an
      account that can log in and accept an invite. This is the end-to-end claim of the
      dossier and must be executed, not reasoned. (**Executed** against a real Postgres —
      see D-11 for the container recipe and the mutation probes.)
- [x] AC-8: `POST /api/admin/users/{id}/activation-links` mints fresh, working tokens,
      invalidates nothing already used, and is rate-limited per target user. (The
      already-consumed half is proven against the real token tables, not mocks.)
- [x] AC-9: No token appears in any audit row, log line, or error body — asserted by a
      test that scans the emitted audit metadata and the captured log output for the
      plaintext token.
- [x] AC-10: Nothing writes an `org_members` or `project_members` row outside
      `_finalize_acceptance` — a test asserts the invariant so a future shortcut has to
      break it deliberately. (An AST sweep of `contexts/`, `app/` and `smap/` against a
      four-entry allowlist: the accept path, the two self-consent create paths, and the
      E2E fixture seeder.)
- [x] AC-11: The two R6.11 citations at `invites.py:96` and `:129` are corrected to R6.02.
      (Four in total — see D-3.)
- [x] AC-12: `docs/operations.md` carries the closed-deployment recipe. (§7a.5, and §7a's
      opening claim that mail-less installs cannot onboard is corrected to point at it.)
- [x] AC-13: Gates green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`,
      `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`, and
      `pnpm run check:openapi-drift` after `gen:api`.
      **Ticked on the second pass, with one standing caveat.** `ruff check` (all passed),
      `ruff format --check` (967 files), `mypy` (962 files, no issues), `pnpm lint`,
      `pnpm typecheck`, `pnpm build`, `check:boundaries-enforced`, `check:bundle-size` and
      `check:type-coverage` (98.61%) are clean. `pnpm test` is **1241/1241 across 197
      files** — FU-7's `SCodeEditor.test.ts` flake did not reproduce in either full run.
      `pytest -q` on the unit tier exits 0 with six environmental skips; it still excludes
      `test_graphrag_builder.py`, which hangs on this host (D-10, pre-existing and
      unrelated). `check:openapi-drift` still cannot execute here — its bash script shells
      out to `python`, which is not on the bash PATH (D-9) — but **both halves of its
      assertion were reproduced by hand on this pass**: re-exporting the spec from the
      backend produced a byte-identical `backend/openapi.json`, and re-running `gen:api`
      left `src/shared/api-client` with an empty `git status`.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1, AC-3, AC-4, AC-5, AC-6, AC-8 | unit (route + service) | `backend/tests/unit/` beside the existing invite and admin-user route tests |
| AC-2 | unit | one test, two fixtures (address with and without a user row), asserting equality of the full response |
| AC-7 | integration | `backend/tests/integration/` — real Postgres; provision, set password, verify, log in, accept an invite |
| AC-9 | unit | capture `audit.emit` calls and loguru output around each mint |
| AC-10 | unit | static or call-graph assertion over the two membership repositories' `add` methods |
| AC-11 | review | the corrected citations |
| AC-12 | review | the documentation section |
| Frontend | component (Vitest) | amended `ProjectMembersView` / `OrgMembersView` tests, new `AdminUsersView` create-dialog test (gate #8) |
| End-to-end | Playwright | `frontend/e2e/` — owner invites, copies the link, a second browser context redeems it |

AC-7 is the one that decides whether this dossier delivered anything. Per
`docs/tasks/BOARD.md`, recent dossiers in this repository have repeatedly closed with the
`integration` tier unrun because Docker was unavailable; if that happens here, leave AC-7
unticked rather than claimed.

## 13. SRS Delta

Two amendments; both describe an added path, neither weakens an existing rule.

**(a) Amend [R6.09] (`REQUIREMENTS.md:261`), replacing the sentence in full:**

> - **[R6.09]** Org invitations: Org Owner sends invite by email. If invitee has no
>   account, the invite link lands on a sign-up page; after sign-up + email verification,
>   they are automatically enrolled in the Org. The invite-creation response additionally
>   returns the accept link to the inviter so an installation without outbound mail can
>   deliver it out of band; the response is identical whether or not the address already
>   has an account, and the link is returned only at creation, never by a read endpoint.
>   An invitee who already has an account also receives the invite in their in-app invite
>   inbox, with no mail involved.

**(b) Insert after [R6.13] (`REQUIREMENTS.md:265`):**

> - **[R6.18]** An Admin may provision an account (email, optional display name) without
>   the account holder present. The account is created `pending` and unverified with no
>   password, and the Admin receives two single-use links to hand over: a set-password
>   link (R6.05 semantics) and an email-verification link (R6.02 semantics). Both may be
>   re-issued on demand. Provisioning does not bypass the email-domain policy of R19a.13,
>   does not create any Org or Project membership, and does not mark the address verified:
>   R6.02's gates apply to the provisioned account exactly as they apply to a
>   self-registered one.

## 14. Open Questions

- **OQ-1** — Should the invite result surface a QR code as well as a copyable link? For a
  classroom the projector is the fastest out-of-band channel. Purely additive; not scoped
  here because it changes the token's exposure profile (a projected screen is a wider
  audience than a clipboard).

## 15. Deviation Log

**Scope.** Two passes. The first covered §6 Backend, the operator documentation and the
API contract (D-8); the second (D-17 onward) built the frontend of §6, which retires
FU-5 and completes the dossier.

- **D-1** — §6 assumed both invite-create routes share `orgs.InviteOut`. They do not:
  `POST /api/projects/{id}/invites` returned a bare `dict[str, str]` carrying only `id`
  and `expires_at`. Rather than bolt `accept_url` onto an untyped dict, the route gained a
  `ProjectInviteOut` mirroring the org shape. Additive at runtime; it also brings the route
  back under the backend rule that a handler returns a Pydantic model, never a raw dict.
- **D-2 — Q-4's rationale is factually wrong, and the decision survives anyway.** Q-4
  declined an invite-only mode because "R19a.13 is already admin-tunable at runtime".
  **No endpoint writes those keys.** `email_domain_policy` reads three Redis keys and
  nothing in the repository sets them; the module docstring promised an "Admin PATCH
  handler (Phase I)" that was never built. The decision is unaffected — the control does
  exist and is tunable, just with `redis-cli` rather than an API — so §7a.5 of
  `docs/operations.md` documents the real mechanism and the misleading docstring is
  corrected in place. FU-6 carries the admin surface.
- **D-3** — AC-11 named two stale R6.11 citations; there were four. The same error sits in
  `invite_service.py`'s module docstring and in `accept_by_token`'s, both describing the
  same gate. Fixing two and leaving two would have left a reader with contradictory
  citations for one rule, so all four now say R6.02.
- **D-4 — a tightening the spec did not ask for.** `POST /api/admin/users/{id}/activation-links`
  refuses an account that can already authenticate — a verified address **and** at least one
  usable credential — with a 409. R6.18 says the links "may be re-issued on demand", which
  this preserves for every account still mid-activation, but an unrestricted version would
  be a button that mints a persistent account-takeover credential for any live user. An
  Admin who must act as a user already has impersonation, which is bounded and separately
  audited. See D-16 for the first, wrong version of this guard.
- **D-5** — §6 said `invitable_org_members` "reads only tenancy tables". It cannot: a live
  pending invite is matched by **email**, because `invites.invitee_user_id` stays NULL
  until acceptance, so excluding already-invited users requires `users`. The query joins
  it directly, following the precedent `_notify_invitee` set (and its comment) rather than
  importing the identity repository layer.
- **D-6** — §8 said "rate limit the link mints" without saying how the refusal surfaces.
  A new `ActivationLinkRateLimited` domain error maps to a 429 Problem carrying
  `retry_after_seconds`. Deliberately *not* the silent-drop shape `register` and
  `request_password_reset` use: those swallow the limit to avoid an enumeration oracle,
  and here the caller is an authenticated Admin with nothing to be denied knowing.
- **D-7** — `verify_email_url` / `password_reset_url` were lifted out of `AuthEmailService`
  into module functions. §6 asked for links "built exactly as the mailers build them"; a
  second copy of the f-string would have satisfied that on the day and drifted later into
  a link the SPA cannot read.
- **D-8 — the frontend is not built.** At the user's direction this pass covers §6 Backend,
  the operator documentation, and the API contract only. `gen:api` was still rerun (the
  contract changed, and leaving the generated client stale would break the drift gate for
  the next person), so the new endpoints are typed and reachable from the client — but no
  view calls them yet. FU-5 carries the UI.
- **D-9** — `pnpm run check:openapi-drift` cannot execute on this host: the script shells
  out to `python`, which is not on the bash PATH here (the same limitation BOARD.md records
  for the member-groups dossier). The gate's actual assertion was reproduced by hand —
  re-export the spec, re-run `gen:api`, confirm `git status` is clean for `openapi.json`
  and `src/shared/api-client` — and came out clean.
- **D-10** — the unit tier was run with `tests/unit/test_graphrag_builder.py` excluded. It
  hangs indefinitely on this host, in isolation as well as in the tier; pre-existing and
  unrelated (recorded as D-7 of `2026-08-16-platform-type-delete-optin-lifecycle`).
- **D-11** — the `db` tier now runs here. One throwaway `pgvector/pgvector:0.8.0-pg16`
  container on port 5433 plus a `redis:7-alpine` on 6380, `SMAP_DB_DSN` / `SMAP_REDIS_DSN`
  pointed at them, `alembic upgrade head` (clean through 0079). Both containers were
  removed afterwards. The only faked collaborator is Vault Transit's JWT signing, which is
  a compose-tier dependency and is not what AC-7 asserts; every account-state gate `login`
  applies runs for real. **Three mutation probes** — marking the provisioned account
  verified at creation, corrupting the accept token, and breaking the org-membership
  correlation of D-12 — each turned the relevant test red for the right reason.
- **D-12 — the security gate found a real hole in this dossier's own reasoning, and it is
  the most important entry here.** §8 and Q-6 both assert that the picker "discloses
  nothing new" because its pool is a subset of `GET /api/orgs/{id}/members`, "already
  readable by any member of that Org". **Capability #14 does not establish org
  membership.** A user invited straight into an org-owned project as its Owner holds #14
  and appears in no `org_members` row, so `GET /api/orgs/{id}/members` is closed to them —
  and the endpoint as first written would have handed them every address in the parent
  Org. That is precisely the disclosure §2 lists as a Goal not to introduce. The caller's
  own org membership is now a predicate of the query (an Admin is exempt per R5.01), and a
  caller outside the Org gets an empty pool: the same shape a user-owned project yields,
  so the two cases are indistinguishable. Proven against real rows rather than compiled
  SQL — the predicate is a correlated `EXISTS` over an aliased self-join, and a wrong
  correlation renders as plausible-looking SQL.
- **D-13** — the pool is bounded with `LIMIT`/`OFFSET` in SQL rather than fetched whole and
  sliced in the route. §7 asked for `PaginationParams`, which the first version applied
  only after loading every member row.
- **D-14** — the quality gate replaced a string-matched status code. `issue_activation_links`
  first raised a plain `ValueError` and the route chose 404 vs 409 by testing
  `"not found" in str(exc)` — copied from `hard_delete_user`, and fragile in exactly the
  way a reworded message exposes. It now raises `AccountAlreadyActivatedError`, and the
  route branches on the type, matching `ban_user`'s existing shape.
- **D-16 — a post-implementation `/code-review` found that D-4's guard did not hold, and
  the account shape it missed is one this repository creates routinely.** The refusal was
  `password_hash is not None and email_verified`. A Google-provisioned account is created
  with `password_hash=None`, `status=ACTIVE`, `email_verified=True`
  (`auth_service.py:529-535`, R6.15), and R6.16 *neutralises* the password of an account
  Google links to — so for every Google user the first conjunct was false, the refusal
  never fired, and an Admin could mint a working set-password link for a fully live
  account. That is the exact takeover primitive D-4 exists to prevent, aimed at users who
  have never had a password. As a side effect the call also burned the target's
  outstanding legitimate reset token.
  The test is "a verified address plus **any** usable credential — a password *or* a linked
  identity", which is the same notion `LastCredentialError` already encodes for unlinking
  (`auth_service.py:626`). The case the fix must not sweep up has its own test: a
  provisioned account that walks the *verify* link first is ACTIVE and verified while
  holding no credential at all, and still needs its set-password link.
  Covered at both tiers and mutation-probed at both: restoring the password-only test turns
  the unit test and the `db` test red. **The lesson for the next reviewer of this file:
  "has a password" is not a synonym for "can log in" in this codebase.**
- **D-17 — the frontend half is built, which closes D-8 and retires FU-5.** Everything §6
  Frontend listed is in: the picker over `GET /api/projects/{id}/invitable-members`, the
  copyable `accept_url` on both member views, the admin create-user dialog and the
  re-issue action, a `useClipboard` in `@shared/composables`, the new keys in both locale
  files, amended component tests, and the Playwright spec of §12
  (`e2e/20-onboarding-without-smtp.spec.ts`). D-18 to D-22 record where it departed from
  the approved §6.
- **D-24 — a post-close `/code-review` found five defects in the frontend diff, and the
  first is the one to carry forward.** `useFocusTrap`'s watcher had no `immediate`, so a
  dialog mounted **already open** never fired it: the `v-if="result"` wrapper with a
  constant `:open="true"` inside — the shape both activation-links dialogs use, and the
  only such shape in the codebase — got no focus move, no body-scroll lock, and nothing
  recorded to restore focus to. Tab walked the page behind the modal. Fixed in the shared
  primitive, not at the two call sites, so the next `:open="true"` is correct by
  construction, and pinned by the regression test `useFocusTrap` never had (probed: reverting
  `immediate` reddens the mounted-open case and leaves the opened-later one green). The
  other four: the project invite card rendered one round trip before its pool and swapped
  the address field for the picker under anyone mid-keystroke (now waits for the query to
  settle); `AdminCreateUserDialog`'s submit button sits in SModal's footer slot, which is
  rendered **outside** the `<form>`, so `type="email"` was inert for anyone who clicked
  rather than pressed Enter and the resulting 422 had no mapped message (now wired through
  the HTML5 `form` attribute, with the 422 mapped); the re-issue guard said "every live
  account" but only excluded deleted ones, so a **banned** account that never set a
  password — which the server guard also reads as "not yet activated" (FU-9) — was offered
  activation links; and two files carried a UTF-8 BOM added by a PowerShell round-trip
  during the build rather than by any intended edit. **The process lesson: never
  round-trip a source file through `Get-Content`/`Set-Content` on this host** — Windows
  PowerShell 5.1 decodes a BOM-less UTF-8 file as ANSI, so the same trip that added those
  BOMs also destroyed three em-dashes, which had to be repaired by hand.
- **D-23 — the browser pass happened, which BOARD.md records as not having happened for
  seven consecutive dossiers in this area.** The compose stack was brought up locally
  (postgres, redis, vault, backend-web; `alembic upgrade head` clean through 0079;
  `smap.bootstrap vault-init` for the transit keys; the two e2e users seeded by hand
  because registration fails closed without the Vault captcha config). All three tests of
  `e2e/20-onboarding-without-smtp.spec.ts` pass against it, and each of the four new
  surfaces was additionally looked at in the browser: the picker with its escape hatch,
  the accept-link card, the admin create-user dialog, and the activation-links dialog with
  both expiries rendered. **One thing the stack surfaced that no test would have:** the
  backend primes its rate-limit policies at boot, so a backend started before
  `alembic upgrade head` leaves that table missing and every later login bounces — which
  is what the first e2e run failed on, not the diff. Restart the backend after migrating.
  Two frontend §12 tiers were also verified for real: the amended component tests, and
  four **mutation probes** (removing the picker's pool gate, always setting the accept
  link, dropping `useClipboard`'s copied-flag reset, and pointing both activation fields
  at the same URL) each turned the intended test red for the intended reason.
- **D-18 — a defect §6 did not know about, in the code the picker had to be gated on.**
  `ProjectMembersView` decided "may I invite" from its own row in the member list
  (`role === 'owner'`). Project ownership is **inherited** ([R5.03]), so an Org Owner
  manages every project of the org while holding no `project_members` row — and the
  invite card was therefore hidden from exactly the people Q-6 designed the picker for.
  The view now reads `ProjectOut.is_moderator` through the existing `useProjectRole`,
  which is the same fix `2026-08-20-orchestration-room-scoped-reads` D-7 made elsewhere
  and whose BOARD.md note says in as many words: do not read the member list. The same
  correction applies to the row actions (promote/demote/remove), which had the identical
  gate. `OrgMembersView` is untouched — org membership is inherited from nowhere, so
  there the member list really is the authority.
- **D-19 — the picker replaces the address field rather than sitting beside it.** §6 said
  "with the typed-address field retained as an alternative"; two always-visible inputs for
  one value is an ambiguous form (which one wins on submit?). The card shows one control
  at a time with a toggle — "invite someone who is not in the organization yet" — and
  falls back to the address field automatically when the pool is empty, which is also what
  a user-owned project and a caller outside the parent Org both produce. Q-6's escape
  hatch is preserved; only its presentation changed.
- **D-20 — an `SCopyField` in `@shared/ui`, beyond the `useClipboard` §6 asked for.** Four
  copy fields ship in this diff (the accept link, and the two admin links on two views)
  and each needs the same label + read-only input + copy button + copied state. The value
  is rendered in a real `<input>` rather than as text on purpose: when the Clipboard API
  is refused — a non-secure origin, a denied permission — the browser's own select-and-copy
  is the only route left, and text in a `<p>` does not give the user one.
- **D-21 — the re-issue action is offered for every live account, not only for one the
  client believes still needs activating.** The server's predicate is D-16's: a verified
  address **and** any usable credential, where "credential" includes a linked Google
  identity — which the client cannot see. Gating the button on `email_verified` would have
  hidden it from the one account D-16 exists to protect: a provisioned user who walked the
  *verification* link first, who is ACTIVE and verified and holds no credential at all and
  still needs their set-password link. So the button is shown for any non-deleted account
  and the 409 is the answer, with its own message pointing at password reset or
  impersonation. **This is D-16's lesson applied on the client: "has a password" and even
  "is verified" are not synonyms for "can log in" here.**
- **D-22 — `useClipboard` did not migrate the three existing hand-rolled sites.** FU-5
  named them as the motivation for the composable, not as deliverables. `useEntityLifecycle`
  and the two conversation-slice sites are unchanged; the conversation ones sit inside
  `ChatroomView.vue`/`ChatroomSettingsView.vue`, which two still-open dossiers on
  BOARD.md are editing, and rewriting them here would hand those a conflict for no
  behavioural gain. FU-12 carries the sweep, including the `useEntityLifecycle` bug it
  will fix on the way.
- **D-15** — a self-audit catch worth recording because the cause was a lint fix: silencing
  ruff's `PLW0108` by replacing `lambda: AsyncMock()` with the bare `AsyncMock` class in a
  test's FastAPI dependency override turned that class's constructor keywords into query
  parameters, and every route test in that file started returning 422. The lesson is
  mechanical — re-run the tests after a lint fix, not before it.

## 16. Follow-ups

- **FU-1** — Bulk onboarding: a CSV or pasted address list producing many invites, or many
  provisioned accounts, in one action. The realistic classroom need; deliberately out of
  scope until the single-item flow is proven.
- **FU-2** — Re-issuing an invite accept link. Today the recovery for a lost link is
  revoke-and-reinvite (§5, Decision 1). If that proves annoying, the shape is the same as
  the admin activation-link re-issue.
- **FU-3** — `_raise_forbidden` is a private helper imported function-locally at
  `invites.py:97` and `:130`. A public equivalent in `shared_kernel.auth.dependencies`
  would retire a small pile of these across the API layer.
- **FU-4** — `NotificationKind.APPROVAL_HUMAN_REQUESTED`
  (`notification/domain/models.py:16`) has no producer outside tests. Either wire it or
  delete it; an enum member that nothing emits reads as a working feature.
- **FU-5** — ~~The frontend half of §6~~. **Built in the second pass; see D-17.**
- **FU-6** — No API writes the email-domain policy of R19a.13 (D-2). An admin endpoint over
  the three `config:email_domain:*` keys would retire the `redis-cli` step in
  `docs/operations.md` §7a.5 and make Q-4's rationale true as written.
- **FU-7** — `src/shared/ui/__tests__/SCodeEditor.test.ts` fails under full-suite load
  ("CodeMirror not mounted yet") and passes in isolation. Unrelated to this diff — nothing
  here touches that component — but it makes `pnpm test` non-deterministic locally.
- **FU-8** — No search filter on the invitable pool. §7 allowed one "if an Org is large";
  the pool is now `LIMIT`ed in SQL (D-13), so a large Org is bounded, but the picker has no
  way to reach a member past the first page other than paging blindly.
- **FU-9** (hardening, no attack path) — `issue_activation_links` does not refuse a
  **banned** account that holds no credential: it is "not yet activated", so links are
  minted for it. Harmless today because both downstream gates refuse independently —
  `login` raises `AccountBanned` after password verification, and `mark_verified` declines
  to promote a banned row — but it offers "activation" for an account that can never
  activate, and it becomes a real hole if either gate is ever relaxed. D-16 is the reason
  to treat this as worth closing rather than tolerating: that guard has already been wrong
  once about which accounts are live.
- **FU-10** (hardening, no attack path) — `POST /api/admin/users` has no per-actor cap
  beyond the global middleware bucket, unlike the activation-link mint. Admin-only, so the
  precondition for abuse is an admin account already lost, but a compromised admin session
  can create accounts at request speed.
- **FU-11** — **The email-domain allowlist fails open out of an LRU cache.** Its three keys
  live in Redis with no TTL under `--maxmemory-policy allkeys-lru`
  (`docker-compose.yml:254`), and an absent `mode` reads as `off`, which admits every
  domain (`email_domain_policy.py`). Memory pressure, a flush, or a restored Redis
  therefore reopens registration silently. Pre-existing and outside this dossier's scope,
  but §7a.5 is the first place that tells an operator to depend on it, so the recipe now
  carries the warning. The fix is either persistence (move the lists to Postgres and mirror
  them, as the rate-limit policies already do) or a startup assertion.
- **FU-12** — Migrate the three hand-rolled `navigator.clipboard` sites onto the new
  `useClipboard` (D-22): `useEntityLifecycle.ts:75`, `ChatroomSettingsView.vue:224`,
  `ChatroomView.vue:848`. Not cosmetic — the first toasts `tenancy.common.loading`
  ("Loading...") when a copy *fails*, which is a wrong message shipped today. The other
  two live in files two open dossiers are editing, so this is worth doing once those land
  rather than racing them. An ESLint rule banning bare `navigator.clipboard` outside the
  composable would make it stick.
- **FU-13** — The picker cannot reach a member past the first page. This is FU-8 seen from
  the client: the endpoint is `LIMIT`ed at 100 (D-13), so a class-sized Org is fine and a
  university-sized one silently truncates the select with no search and no paging. The fix
  is one query parameter on the endpoint plus an `SSearchInput` above the control.
- **FU-14** — `AdminUserActions` now renders five equal-weight buttons in a flat row, and
  the re-issue one is the least-used of them. A dropdown for the rare half would be a
  better shape. Presentational only, and deliberately not done here because it would touch
  every action in the component rather than the one this dossier added.
