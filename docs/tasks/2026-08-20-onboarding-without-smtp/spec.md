---
type: feature
status: approved
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

- [ ] AC-1: `POST /api/orgs/{id}/invites` and `POST /api/projects/{id}/invites` return an
      `accept_url` that redeems successfully through `POST /api/invites/accept-by-token`.
- [ ] AC-2: The response is identical in shape and content whether or not the invitee's
      address has an account — a test asserts both branches, including that no field, no
      status code and no header differs.
- [ ] AC-3: `accept_url` is absent from `GET /api/invites` and from every other invite
      read path.
- [ ] AC-4: `GET /api/projects/{id}/invitable-members` returns parent-Org members who are
      neither project members nor holders of a live pending invite; returns 200 with an
      empty list for a user-owned project; refuses a caller without capability #14.
- [ ] AC-5: `POST /api/admin/users` creates a `pending`, unverified, password-less account,
      returns both links, and is refused for a non-admin.
- [ ] AC-6: An address the deployment's email-domain policy denies is refused by
      `POST /api/admin/users`.
- [ ] AC-7: Walking the returned links in order — set password, then verify — produces an
      account that can log in and accept an invite. This is the end-to-end claim of the
      dossier and must be executed, not reasoned.
- [ ] AC-8: `POST /api/admin/users/{id}/activation-links` mints fresh, working tokens,
      invalidates nothing already used, and is rate-limited per target user.
- [ ] AC-9: No token appears in any audit row, log line, or error body — asserted by a
      test that scans the emitted audit metadata and the captured log output for the
      plaintext token.
- [ ] AC-10: Nothing writes an `org_members` or `project_members` row outside
      `_finalize_acceptance` — a test asserts the invariant so a future shortcut has to
      break it deliberately.
- [ ] AC-11: The two R6.11 citations at `invites.py:96` and `:129` are corrected to R6.02.
- [ ] AC-12: `docs/operations.md` carries the closed-deployment recipe.
- [ ] AC-13: Gates green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`,
      `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`, and
      `pnpm run check:openapi-drift` after `gen:api`.

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

Appended by `/build`. Empty means the implementation matches this spec exactly.

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
