# 03 -- Identity

> Authentication and account management views.
> Covers registration, login, email verification, password reset, account settings, sessions, and account deletion.
> All pages target production quality directly.

---

## 1. RegisterView

**File**: `src/slices/identity/views/RegisterView.vue`
**Route**: `/register` (name: `identity.register`)
**Layout**: `AuthLayout` (centered card, max-width 420px)
**Auth**: public (redirects to `/orgs` if already authenticated)

### 1.1 Wireframe

```
┌─────────────────────────────────────────┐
│            --color-surface bg           │
│                                         │
│               SMAP                      │
│         ┌───────────────────┐           │
│         │  Create Account   │           │
│         │                   │           │
│         │  Email            │           │
│         │  ┌──────────────┐ │           │
│         │  │              │ │           │
│         │  └──────────────┘ │           │
│         │                   │           │
│         │  Password         │           │
│         │  ┌──────────────┐ │           │
│         │  │         [eye]│ │           │
│         │  └──────────────┘ │           │
│         │  Min 10 chars,    │           │
│         │  letter+digit+    │           │
│         │  symbol           │           │
│         │                   │           │
│         │  ┌──────────────┐ │           │
│         │  │  [hCaptcha]  │ │           │
│         │  └──────────────┘ │           │
│         │                   │           │
│         │  [  Create Account  ]         │
│         │                   │           │
│         └───────────────────┘           │
│                                         │
│        Already have an account?         │
│              Log in                     │
│                                         │
└─────────────────────────────────────────┘
```

### 1.2 Fields

| Field | Component | Type | Autocomplete | Required | Constraints | i18n Label |
|-------|-----------|------|--------------|----------|-------------|------------|
| `email` | `SInput` | `email` | `email` | yes | valid email, max 320 chars | `identity.register.email` |
| `password` | `SInput` | `password` | `new-password` | yes | min 10, max 1024, at least 1 letter + 1 digit + 1 symbol | `identity.register.password` |
| captcha | `CaptchaWidget` | -- | -- | conditional | required when `captchaConfig.mode !== 'off'` | -- |

**Password help text**: displayed below the password field in `--color-muted` at 12px. Text: `$t('identity.register.passwordHelp')` -- "At least 10 characters with a letter, digit, and symbol."

### 1.3 Behavior

**On mount**:
1. Call `GET /api/auth/captcha-config` to retrieve `CaptchaConfigOut`.
2. If `mode` is `off`, hide the `CaptchaWidget`.
3. If `mode` is `hcaptcha` or `turnstile`, render `CaptchaWidget` with `provider` and `sitekey`.
4. Focus the email field on mount.

**On submit**:
1. Frontend validation: check all fields are non-empty; check password meets minimum length.
2. If CAPTCHA is enabled and no `captchaToken` has been received, show field-level error on CAPTCHA area.
3. Call `POST /api/auth/register` with `{ email, password, captcha_token }`.
4. On `202`: redirect to `identity.login` with `query: { pendingVerify: '1' }`.

**Submit button**: disabled while `submitting` is true. Shows `SLoadingSpinner` inline when loading. Text changes to `$t('identity.register.submitting')`.

### 1.4 Error Handling

All server errors follow RFC 7807 Problem format. Errors are displayed using `SAlert` variant `error` placed above the submit button.

| Error Slug | HTTP | Display | Recovery |
|------------|------|---------|----------|
| `auth/email-taken` | 409 | `$t('identity.errors.emailTaken')` | Clear email field, refocus it |
| `auth/domain-denied` | 422 | `$t('identity.errors.domainDenied')` | Clear email field |
| `auth/password-weak` | 422 | `$t('identity.errors.weakPassword')` with `detail` from server | Clear password, refocus it |
| `auth/captcha-required` | 400 | `$t('identity.errors.captchaRequired')` | Reset captcha widget |
| `auth/email-invalid` | 422 | `$t('identity.errors.emailInvalid')` | Clear email field |
| 429 (rate limit) | 429 | `$t('identity.errors.rateLimit')` | Disable form for `Retry-After` seconds, show countdown |
| Network / 5xx | -- | `$t('identity.errors.generic')` | Enable retry |

**Field-level validation** (checked on blur and before submit):
- Empty email: `$t('identity.validation.emailRequired')`
- Invalid email format: `$t('identity.validation.emailFormat')`
- Empty password: `$t('identity.validation.passwordRequired')`
- Password too short (< 10): `$t('identity.validation.passwordMinLength')`

Field errors are displayed via `SFormField`'s `error` prop, which renders error text below the input with `role="alert"`.

### 1.5 Loading & Success States

| State | Visual |
|-------|--------|
| Initial | Form rendered, email field focused |
| CAPTCHA loading | `SLoadingSpinner` in captcha area while script loads |
| Submitting | Button shows spinner + `$t('identity.register.submitting')`, all fields disabled |
| Success | Redirect to `identity.login?pendingVerify=1` (no success message on this page) |
| Captcha config fetch failed | CAPTCHA area hidden (fail-open per backend design), registration proceeds without CAPTCHA |

### 1.6 Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| >= 480px | Card centered with `--shadow-md`, max-width 420px, padding 32px |
| < 480px | Card full-width, no shadow, padding 24px 16px, border-radius 0 |

Card vertical centering uses `min-height: 100dvh` with flexbox. On small viewports the card scrolls naturally if content exceeds viewport height.

### 1.7 Accessibility

- `<form>` has `aria-labelledby` pointing to the heading.
- Each `SFormField` associates its `<label>` with the input via `for`/`id`.
- Password field has `aria-describedby` linking to the help text and any error message.
- Error messages use `role="alert"` and `aria-live="assertive"`.
- CAPTCHA widget: add `aria-label="$t('identity.register.captchaLabel')"` to the container div.
- Submit button: when loading, `aria-busy="true"`.
- "Log in" link: standard `<RouterLink>`, keyboard-focusable.
- Tab order: email -> password -> show/hide toggle -> captcha -> submit -> log in link.
- Focus ring: `--focus-ring` on all interactive elements.

### 1.8 Components Used

| Component | Usage |
|-----------|-------|
| `SFormField` | Wraps each input with label, error, help text |
| `SInput` | Email and password fields |
| `SButton` | Submit button (variant: `primary`, size: `md`, `loading` prop) |
| `SAlert` | Server error display (variant: `error`) |
| `SLoadingSpinner` | Inline in submit button, captcha loading |
| `CaptchaWidget` | CAPTCHA challenge (hCaptcha or Turnstile) |

---

## 2. LoginView

**File**: `src/slices/identity/views/LoginView.vue`
**Route**: `/login` (name: `identity.login`)
**Layout**: `AuthLayout`
**Auth**: public (redirects to `/orgs` if already authenticated)

### 2.1 Wireframe

```
┌─────────────────────────────────────────┐
│            --color-surface bg           │
│                                         │
│               SMAP                      │
│         ┌───────────────────┐           │
│         │                   │           │
│         │  [info] Check     │  <-- conditional: pendingVerify
│         │  your email to    │
│         │  verify account   │
│         │                   │           │
│         │  Log In           │           │
│         │                   │           │
│         │  Email            │           │
│         │  ┌──────────────┐ │           │
│         │  │              │ │           │
│         │  └──────────────┘ │           │
│         │                   │           │
│         │  Password         │           │
│         │  ┌──────────────┐ │           │
│         │  │         [eye]│ │           │
│         │  └──────────────┘ │           │
│         │                   │           │
│         │  Forgot password? │ <-- link, right-aligned
│         │                   │           │
│         │  [    Log In      ]           │
│         │                   │           │
│         └───────────────────┘           │
│                                         │
│        Don't have an account?           │
│             Register                    │
│                                         │
└─────────────────────────────────────────┘
```

### 2.2 Fields

| Field | Component | Type | Autocomplete | Required | Constraints | i18n Label |
|-------|-----------|------|--------------|----------|-------------|------------|
| `email` | `SInput` | `email` | `email` | yes | valid email | `identity.login.email` |
| `password` | `SInput` | `password` | `current-password` | yes | max 1024 (no min on login) | `identity.login.password` |

### 2.3 Behavior

**On mount**:
1. If `route.query.pendingVerify === '1'`, show an `SAlert` (variant: `info`) above the form: `$t('identity.login.pendingVerify')` -- "Check your email and click the verification link before logging in."
2. Focus the email field.
3. If already authenticated (session store has valid token), redirect to `/orgs`.

**On submit**:
1. Frontend validation: both fields non-empty.
2. Call `session.login(email, password)` via `useSessionStore`, which calls `POST /api/auth/login`.
3. On success (`200`, `TokenPairOut`): store tokens, call `GET /api/auth/me` to populate user state, then `safeRedirect(route.query.redirect)` (defaults to `/orgs`).
4. `safeRedirect` validates the redirect URL is same-origin to prevent open-redirect attacks.

**Forgot password link**: `<RouterLink :to="{ name: 'identity.passwordResetRequest' }">`, right-aligned below the password field, styled as `--color-accent` text link at 14px.

### 2.4 Error Handling

| Error Slug | HTTP | Display | Recovery |
|------------|------|---------|----------|
| `auth/invalid-credentials` | 401 | `$t('identity.errors.invalidCredentials')` -- "Incorrect email or password." | Clear password field, refocus it |
| `auth/lockout` | 429 | `$t('identity.errors.lockout')` with remaining seconds from `Retry-After` header -- "Too many failed attempts. Try again in {seconds}s." | Disable form, show countdown timer |
| `auth/email-unverified` | 403 | `$t('identity.errors.emailUnverified')` -- "Please verify your email before logging in." with "Resend" link | Show resend verification link (future enhancement) |
| `auth/banned` | 403 | `$t('identity.errors.banned')` | No recovery; link to support |
| `auth/deleted` | 410 | `$t('identity.errors.accountDeleted')` | No recovery |
| 429 (IP rate limit) | 429 | `$t('identity.errors.rateLimit')` | Disable form, countdown |
| Network / 5xx | -- | `$t('identity.errors.generic')` | Enable retry |

Error display: `SAlert` variant `error` between the form fields and the submit button.

**Lockout countdown**: when lockout error is received, parse `Retry-After` header (seconds). Display a live countdown in the error message. Disable the submit button until the countdown reaches zero. Use `setInterval` with 1-second ticks, cleared on unmount.

### 2.5 Loading & Success States

| State | Visual |
|-------|--------|
| Initial | Form rendered, email focused. Optional pendingVerify info alert |
| Submitting | Button shows spinner + `$t('identity.login.submitting')`, fields disabled |
| Success | Redirect to `safeRedirect(route.query.redirect)` or `/orgs` |
| Lockout | Form disabled, countdown timer in error alert |

### 2.6 Responsive Behavior

Same as RegisterView (inherits AuthLayout responsive rules).

### 2.7 Accessibility

- `<form>` has `aria-labelledby` pointing to the heading.
- `pendingVerify` alert: `role="status"` (informational, not disruptive).
- Error alert: `role="alert"`, `aria-live="assertive"`.
- "Forgot password?" link: descriptive text, no `title` attribute needed.
- Tab order: email -> password -> show/hide toggle -> forgot password link -> submit -> register link.
- Lockout state: disabled fields have `aria-disabled="true"`, countdown announced via `aria-live="polite"` region.

### 2.8 Components Used

| Component | Usage |
|-----------|-------|
| `SFormField` | Wraps each input |
| `SInput` | Email and password fields |
| `SButton` | Submit button (variant: `primary`, `loading` prop) |
| `SAlert` | pendingVerify info message, server error display |
| `SLoadingSpinner` | Inline in submit button |

---

## 3. VerifyEmailView

**File**: `src/slices/identity/views/VerifyEmailView.vue`
**Route**: `/verify-email` (name: `identity.verifyEmail`)
**Layout**: `AuthLayout`
**Auth**: public (works for both authenticated and unauthenticated users)

### 3.1 Wireframe

```
State: verifying
┌─────────────────────────────────────────┐
│               SMAP                      │
│         ┌───────────────────┐           │
│         │                   │           │
│         │  Verify Email     │           │
│         │                   │           │
│         │   [spinner]       │           │
│         │   Verifying...    │           │
│         │                   │           │
│         └───────────────────┘           │
└─────────────────────────────────────────┘

State: success
┌─────────────────────────────────────────┐
│               SMAP                      │
│         ┌───────────────────┐           │
│         │                   │           │
│         │  Verify Email     │           │
│         │                   │           │
│         │  [CheckCircle]    │           │
│         │  Email verified   │           │
│         │  successfully.    │           │
│         │                   │           │
│         │  [  Continue  ]   │           │
│         │                   │           │
│         └───────────────────┘           │
└─────────────────────────────────────────┘

State: failure
┌─────────────────────────────────────────┐
│               SMAP                      │
│         ┌───────────────────┐           │
│         │                   │           │
│         │  Verify Email     │           │
│         │                   │           │
│         │  [XCircle]        │           │
│         │  Verification     │           │
│         │  failed. The link │           │
│         │  may be expired.  │           │
│         │                   │           │
│         │  [  Log In  ]     │           │
│         │                   │           │
│         └───────────────────┘           │
└─────────────────────────────────────────┘
```

### 3.2 Fields

No form fields. This is a status display page.

### 3.3 Behavior

**On mount**:
1. Extract token from URL fragment: `new URLSearchParams(window.location.hash.slice(1)).get('token')`.
   - SEC-8: Token is in the fragment (`#token=...`), never in the query string. Fragments are not sent to the server in HTTP requests, preventing token leakage via referrer headers or server logs.
2. If no token found, immediately transition to `failure` state.
3. Set state to `verifying`.
4. Call `POST /api/auth/verify-email` with `{ token }`.
5. On `200`: transition to `success`. If user is authenticated (session store has tokens), call `session.refreshMe()` to update the `email_verified` flag in the local user state.
6. On error: transition to `failure`.

**Success state actions**:
- If authenticated: "Continue" button navigates to `/orgs`.
- If unauthenticated: "Log in" button navigates to `identity.login`.

**Failure state actions**:
- "Log in" button navigates to `identity.login`.

### 3.4 Error Handling

| Error Slug | HTTP | Display |
|------------|------|---------|
| `auth/token-invalid` | 400 | Failure state: `$t('identity.verifyEmail.invalidToken')` -- "This verification link is invalid." |
| `auth/token-expired` | 401 | Failure state: `$t('identity.verifyEmail.expiredToken')` -- "This verification link has expired. Please register again." |
| Network / 5xx | -- | Failure state: `$t('identity.errors.generic')` |

No inline field errors (no form). The entire card switches to the failure visual state.

### 3.5 Loading & Success States

| State | Visual |
|-------|--------|
| `verifying` | `SLoadingSpinner` with text `$t('identity.verifyEmail.verifying')`, centered in card |
| `success` | `CheckCircleIcon` (24/outline, `--color-success`), success message, action button |
| `failure` | `XCircleIcon` (24/outline, `--color-danger`), error message, "Log in" button |

### 3.6 Responsive Behavior

Same as AuthLayout defaults. Card content is minimal and renders well at all sizes.

### 3.7 Accessibility

- State transitions announced via `aria-live="polite"` region wrapping the status content.
- `SLoadingSpinner` has `role="status"`.
- Icons are decorative: `aria-hidden="true"`.
- Action buttons are standard `SButton`, keyboard-focusable and auto-focused on state transition.

### 3.8 Components Used

| Component | Usage |
|-----------|-------|
| `SButton` | "Continue" / "Log in" action buttons (variant: `primary`) |
| `SLoadingSpinner` | Verifying state indicator |
| `CheckCircleIcon` | Success state icon (from `@heroicons/vue/24/outline`) |
| `XCircleIcon` | Failure state icon (from `@heroicons/vue/24/outline`) |

---

## 4. PasswordResetRequestView

**File**: `src/slices/identity/views/PasswordResetRequestView.vue`
**Route**: `/password-reset` (name: `identity.passwordResetRequest`)
**Layout**: `AuthLayout`
**Auth**: public

### 4.1 Wireframe

```
State: form
┌─────────────────────────────────────────┐
│               SMAP                      │
│         ┌───────────────────┐           │
│         │                   │           │
│         │  Reset Password   │           │
│         │                   │           │
│         │  Enter your email │           │
│         │  and we'll send   │           │
│         │  a reset link.    │           │
│         │                   │           │
│         │  Email            │           │
│         │  ┌──────────────┐ │           │
│         │  │              │ │           │
│         │  └──────────────┘ │           │
│         │                   │           │
│         │  [ Send Reset Link ]          │
│         │                   │           │
│         └───────────────────┘           │
│                                         │
│          Back to Log In                 │
│                                         │
└─────────────────────────────────────────┘

State: sent
┌─────────────────────────────────────────┐
│               SMAP                      │
│         ┌───────────────────┐           │
│         │                   │           │
│         │  Check Your Email │           │
│         │                   │           │
│         │  [EnvelopeIcon]   │           │
│         │  If an account    │           │
│         │  with that email  │           │
│         │  exists, we sent  │           │
│         │  a reset link.    │           │
│         │                   │           │
│         │  [  Back to Login ]           │
│         │                   │           │
│         └───────────────────┘           │
└─────────────────────────────────────────┘
```

### 4.2 Fields

| Field | Component | Type | Autocomplete | Required | Constraints | i18n Label |
|-------|-----------|------|--------------|----------|-------------|------------|
| `email` | `SInput` | `email` | `email` | yes | valid email | `identity.passwordReset.email` |

### 4.3 Behavior

**On mount**: focus the email field.

**On submit**:
1. Frontend validation: email is non-empty and passes format check.
2. Call `POST /api/auth/request-password-reset` with `{ email }`.
3. Server always returns `202` (anti-enumeration: never reveals whether the email exists).
4. Transition to `sent` state regardless of server response.
5. Clear the email from local state (do not retain it in the URL or DOM).

**Anti-enumeration note**: the UI deliberately shows the same confirmation message whether or not the email is registered. Error display is intentionally omitted for the success/failure distinction. Only network errors and rate limits surface to the user.

### 4.4 Error Handling

| Condition | Display |
|-----------|---------|
| 429 (rate limit) | `SAlert` variant `warning`: `$t('identity.errors.resetRateLimit')` -- "Too many reset requests. Please try again later." Stay on form state |
| Network / 5xx | `SAlert` variant `error`: `$t('identity.errors.generic')`. Stay on form state |

No field-level server errors (endpoint is intentionally opaque).

### 4.5 Loading & Success States

| State | Visual |
|-------|--------|
| Initial | Form rendered, email focused |
| Submitting | Button shows spinner, field disabled |
| Sent | Card switches to confirmation view with `EnvelopeIcon` (24/outline, `--color-accent`), confirmation text, and "Back to Login" button |

### 4.6 Responsive Behavior

Same as AuthLayout defaults.

### 4.7 Accessibility

- Form state: standard form accessibility via `SFormField`.
- Sent state: `aria-live="polite"` announces the confirmation message.
- `EnvelopeIcon`: `aria-hidden="true"` (decorative).
- "Back to Login" link and "Back to Log In" footer link both navigate to `identity.login`.

### 4.8 Components Used

| Component | Usage |
|-----------|-------|
| `SFormField` | Wraps email input |
| `SInput` | Email field |
| `SButton` | Submit button (variant: `primary`, `loading` prop) |
| `SAlert` | Rate limit / network error display |
| `EnvelopeIcon` | Confirmation state icon (from `@heroicons/vue/24/outline`) |

---

## 5. PasswordResetConfirmView

**File**: `src/slices/identity/views/PasswordResetConfirmView.vue`
**Route**: `/password-reset/confirm` (name: `identity.passwordResetConfirm`)
**Layout**: `AuthLayout`
**Auth**: public

### 5.1 Wireframe

```
┌─────────────────────────────────────────┐
│               SMAP                      │
│         ┌───────────────────┐           │
│         │                   │           │
│         │  Set New Password │           │
│         │                   │           │
│         │  New Password     │           │
│         │  ┌──────────────┐ │           │
│         │  │         [eye]│ │           │
│         │  └──────────────┘ │           │
│         │  At least 10      │           │
│         │  chars, letter+   │           │
│         │  digit+symbol     │           │
│         │                   │           │
│         │  Confirm Password │           │
│         │  ┌──────────────┐ │           │
│         │  │         [eye]│ │           │
│         │  └──────────────┘ │           │
│         │                   │           │
│         │  [ Reset Password ]           │
│         │                   │           │
│         └───────────────────┘           │
└─────────────────────────────────────────┘
```

### 5.2 Fields

| Field | Component | Type | Autocomplete | Required | Constraints | i18n Label |
|-------|-----------|------|--------------|----------|-------------|------------|
| `newPassword` | `SInput` | `password` | `new-password` | yes | min 10, max 1024, letter + digit + symbol | `identity.passwordReset.newPassword` |
| `confirmPassword` | `SInput` | `password` | `new-password` | yes | must match `newPassword` | `identity.passwordReset.confirmPassword` |

**Note**: The backend schema (`PasswordResetIn`) only requires `token` and `new_password`. The `confirmPassword` field is frontend-only for usability and is validated entirely on the client side.

### 5.3 Behavior

**On mount**:
1. Extract token from URL fragment: `new URLSearchParams(window.location.hash.slice(1)).get('token')`.
2. If no token found, show error state: `$t('identity.passwordReset.invalidLink')` with a "Request New Link" button linking to `identity.passwordResetRequest`.
3. Focus the new password field.

**On submit**:
1. Frontend validation: password meets minimum length, confirm matches.
2. Call `POST /api/auth/reset-password` with `{ token, new_password }`.
3. On `204`: redirect to `identity.login` with flash query `?passwordReset=1`.

**Password match validation**: checked on blur of the confirm field and on submit. Mismatch displays `$t('identity.validation.passwordMismatch')` via `SFormField` error prop.

### 5.4 Error Handling

| Error Slug | HTTP | Display | Recovery |
|------------|------|---------|----------|
| `auth/token-invalid` | 400 | `SAlert` error: `$t('identity.passwordReset.invalidToken')` -- "This reset link is invalid." with "Request New Link" button | Link to `identity.passwordResetRequest` |
| `auth/token-expired` | 401 | `SAlert` error: `$t('identity.passwordReset.expiredToken')` -- "This reset link has expired." with "Request New Link" button | Link to `identity.passwordResetRequest` |
| `auth/password-weak` | 422 | `SAlert` error: `$t('identity.errors.weakPassword')` with server `detail` | Clear password fields, refocus |
| Network / 5xx | -- | `SAlert` error: `$t('identity.errors.generic')` | Enable retry |

### 5.5 Loading & Success States

| State | Visual |
|-------|--------|
| Initial | Form rendered, new password focused |
| No token | Error card: `ExclamationTriangleIcon`, "Invalid reset link" message, "Request New Link" button |
| Submitting | Button shows spinner, fields disabled |
| Success | Redirect to `identity.login?passwordReset=1` |

The login page shows `SAlert` info: `$t('identity.login.passwordResetSuccess')` -- "Password reset successfully. Log in with your new password." when `route.query.passwordReset === '1'`.

### 5.6 Responsive Behavior

Same as AuthLayout defaults.

### 5.7 Accessibility

- Both password fields have `aria-describedby` linking to help text.
- Confirm password field: `aria-describedby` also links to the match error when visible.
- Token error state: auto-focuses the "Request New Link" button for keyboard users.
- All error messages: `role="alert"`, `aria-live="assertive"`.

### 5.8 Components Used

| Component | Usage |
|-----------|-------|
| `SFormField` | Wraps each password input |
| `SInput` | New password and confirm password fields |
| `SButton` | Submit button (variant: `primary`, `loading` prop), "Request New Link" button (variant: `secondary`) |
| `SAlert` | Server error display, token error display |
| `ExclamationTriangleIcon` | Token error state icon (from `@heroicons/vue/24/outline`) |

---

## 6. ChangePasswordView

**File**: `src/slices/identity/views/ChangePasswordView.vue`
**Route**: `/account/password` (name: `identity.changePassword`)
**Layout**: `AppShell` (authenticated, inside sidebar + top bar)
**Auth**: required

### 6.1 Wireframe

```
┌──────────────────────────────────────────────────┐
│  Top Bar                                          │
├────────────┬─────────────────────────────────────┤
│            │                                     │
│  Sidebar   │  Change Password                    │
│            │  ─────────────────────────           │
│            │                                     │
│            │  ┌─────────────────────────────┐    │
│            │  │                             │    │
│            │  │  Current Password           │    │
│            │  │  ┌───────────────────┐      │    │
│            │  │  │             [eye] │      │    │
│            │  │  └───────────────────┘      │    │
│            │  │                             │    │
│            │  │  New Password               │    │
│            │  │  ┌───────────────────┐      │    │
│            │  │  │             [eye] │      │    │
│            │  │  └───────────────────┘      │    │
│            │  │  At least 10 chars,         │    │
│            │  │  letter+digit+symbol        │    │
│            │  │                             │    │
│            │  │  Confirm New Password       │    │
│            │  │  ┌───────────────────┐      │    │
│            │  │  │             [eye] │      │    │
│            │  │  └───────────────────┘      │    │
│            │  │                             │    │
│            │  │  [ Change Password ]        │    │
│            │  │                             │    │
│            │  └─────────────────────────────┘    │
│            │                                     │
│            │  Changing your password will sign    │
│            │  you out of all sessions.            │
│            │                                     │
├────────────┴─────────────────────────────────────┤
```

### 6.2 Fields

| Field | Component | Type | Autocomplete | Required | Constraints | i18n Label |
|-------|-----------|------|--------------|----------|-------------|------------|
| `currentPassword` | `SInput` | `password` | `current-password` | yes | max 1024 | `identity.changePassword.current` |
| `newPassword` | `SInput` | `password` | `new-password` | yes | min 10, max 1024, letter + digit + symbol | `identity.changePassword.new` |
| `confirmPassword` | `SInput` | `password` | `new-password` | yes | must match `newPassword` | `identity.changePassword.confirm` |

### 6.3 Behavior

**Layout context**: this view renders inside `AppShell`. It uses `SPageHeader` for the title and wraps the form in an `SCard` with max-width 480px.

**On mount**: focus the current password field.

**On submit**:
1. Frontend validation: all fields non-empty, new password meets policy, confirm matches.
2. Additional check: new password must differ from current password.
3. Call `POST /api/auth/change-password` with `{ current_password, new_password }`.
4. On `204`: the server invalidates all sessions. Call `session.clear()` to remove local tokens, then redirect to `identity.login` with flash `?passwordChanged=1`.

**Warning text**: below the card, render a paragraph in `--color-muted` at 14px: `$t('identity.changePassword.warning')` -- "Changing your password will sign you out of all active sessions."

### 6.4 Error Handling

| Error Slug | HTTP | Display | Recovery |
|------------|------|---------|----------|
| `auth/invalid-credentials` | 401 | `SFormField` error on current password: `$t('identity.errors.invalidCredentials')` -- "Current password is incorrect." | Clear current password, refocus it |
| `auth/password-weak` | 422 | `SAlert` error: `$t('identity.errors.weakPassword')` with server `detail` | Clear new password fields, refocus |
| 429 (rate limit) | 429 | `SAlert` warning: `$t('identity.errors.rateLimit')` | Disable form, countdown |
| Network / 5xx | -- | `SAlert` error: `$t('identity.errors.generic')` | Enable retry |

**Frontend-only validations**:
- Same password: `$t('identity.validation.passwordSame')` -- "New password must be different from current password."
- Confirm mismatch: `$t('identity.validation.passwordMismatch')`

### 6.5 Loading & Success States

| State | Visual |
|-------|--------|
| Initial | Form rendered inside card, current password focused |
| Submitting | Button shows spinner, fields disabled |
| Success | `session.clear()`, redirect to `identity.login?passwordChanged=1` |

### 6.6 Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| >= 768px | Card max-width 480px, left-aligned within content area padding |
| < 768px | Card full-width within content padding (16px) |

### 6.7 Accessibility

- `SPageHeader` renders an `<h1>` for the page title.
- Warning text linked to the form via `aria-describedby`.
- Password fields: `aria-describedby` includes help text and error IDs.
- Confirm field: live validation feedback on blur.

### 6.8 Components Used

| Component | Usage |
|-----------|-------|
| `SPageHeader` | Page title "Change Password" |
| `SCard` | Form container |
| `SFormField` | Wraps each password input |
| `SInput` | Three password fields |
| `SButton` | Submit button (variant: `primary`, `loading` prop) |
| `SAlert` | Server error display |

---

## 7. ChangeEmailView

**File**: `src/slices/identity/views/ChangeEmailView.vue`
**Route**: `/account/email` (name: `identity.changeEmail`)
**Layout**: `AppShell`
**Auth**: required

### 7.1 Wireframe

```
State: form
┌──────────────────────────────────────────────────┐
│  Top Bar                                          │
├────────────┬─────────────────────────────────────┤
│            │                                     │
│  Sidebar   │  Change Email                       │
│            │  ─────────────────────────           │
│            │                                     │
│            │  ┌─────────────────────────────┐    │
│            │  │                             │    │
│            │  │  Current email:             │    │
│            │  │  user@example.com           │    │
│            │  │                             │    │
│            │  │  New Email                  │    │
│            │  │  ┌───────────────────┐      │    │
│            │  │  │                   │      │    │
│            │  │  └───────────────────┘      │    │
│            │  │                             │    │
│            │  │  Password                   │    │
│            │  │  ┌───────────────────┐      │    │
│            │  │  │             [eye] │      │    │
│            │  │  └───────────────────┘      │    │
│            │  │  Confirm your identity      │    │
│            │  │                             │    │
│            │  │  [ Update Email ]           │    │
│            │  │                             │    │
│            │  └─────────────────────────────┘    │
│            │                                     │
├────────────┴─────────────────────────────────────┤

State: done
┌──────────────────────────────────────────────────┐
│  Top Bar                                          │
├────────────┬─────────────────────────────────────┤
│            │                                     │
│  Sidebar   │  Change Email                       │
│            │  ─────────────────────────           │
│            │                                     │
│            │  ┌─────────────────────────────┐    │
│            │  │                             │    │
│            │  │  [EnvelopeIcon]             │    │
│            │  │                             │    │
│            │  │  Verification email sent    │    │
│            │  │  to new@example.com.        │    │
│            │  │  Click the link in the      │    │
│            │  │  email to confirm.          │    │
│            │  │                             │    │
│            │  └─────────────────────────────┘    │
│            │                                     │
├────────────┴─────────────────────────────────────┤
```

### 7.2 Fields

| Field | Component | Type | Autocomplete | Required | Constraints | i18n Label |
|-------|-----------|------|--------------|----------|-------------|------------|
| `newEmail` | `SInput` | `email` | `email` | yes | valid email, max 320 chars | `identity.changeEmail.newEmail` |
| `password` | `SInput` | `password` | `current-password` | yes | max 1024 | `identity.changeEmail.password` |

### 7.3 Behavior

**On mount**:
1. Display current email from `session.user.email` in a read-only line above the form.
2. Focus the new email field.

**On submit**:
1. Frontend validation: new email is non-empty and valid format, password is non-empty.
2. Additional check: new email must differ from current email.
3. Call `POST /api/auth/change-email` with `{ new_email, password }`.
4. On `204`: transition to `done` state. Show confirmation message with the submitted email address.
5. The email is not changed yet. A verification link is sent to the new email. The user must click it to complete the change.

**Current email display**: rendered as a `<p>` with label `$t('identity.changeEmail.currentLabel')` in `--color-muted` and value in `--color-fg`, 14px, within the card above the form fields.

### 7.4 Error Handling

| Error Slug | HTTP | Display | Recovery |
|------------|------|---------|----------|
| `auth/invalid-credentials` | 401 | `SFormField` error on password: `$t('identity.errors.invalidCredentials')` | Clear password, refocus it |
| `auth/email-taken` | 409 | `SAlert` error: `$t('identity.errors.emailTaken')` | Clear new email, refocus it |
| `auth/domain-denied` | 422 | `SAlert` error: `$t('identity.errors.domainDenied')` | Clear new email |
| `auth/email-invalid` | 422 | `SAlert` error: `$t('identity.errors.emailInvalid')` | Clear new email |
| 429 (rate limit) | 429 | `SAlert` warning: `$t('identity.errors.rateLimit')` | Disable form, countdown |
| Network / 5xx | -- | `SAlert` error: `$t('identity.errors.generic')` | Enable retry |

**Frontend-only validation**:
- Same email: `$t('identity.validation.emailSame')` -- "New email must be different from current email."

### 7.5 Loading & Success States

| State | Visual |
|-------|--------|
| Initial | Form rendered in card, new email focused |
| Submitting | Button shows spinner, fields disabled |
| Done | Card content switches to confirmation: `EnvelopeIcon` (24/outline, `--color-accent`), confirmation text including the new email address |

The `done` state stays on the page. No redirect. User must check their new email and click the verification link.

### 7.6 Responsive Behavior

Same as ChangePasswordView: card max-width 480px, full-width below 768px.

### 7.7 Accessibility

- Current email display: use `<dl>` / `<dt>` / `<dd>` for semantics.
- `done` state: `aria-live="polite"` announces the confirmation.
- `EnvelopeIcon`: `aria-hidden="true"`.

### 7.8 Components Used

| Component | Usage |
|-----------|-------|
| `SPageHeader` | Page title "Change Email" |
| `SCard` | Form container |
| `SFormField` | Wraps each input |
| `SInput` | New email and password fields |
| `SButton` | Submit button (variant: `primary`, `loading` prop) |
| `SAlert` | Server error display |
| `EnvelopeIcon` | Confirmation state icon (from `@heroicons/vue/24/outline`) |

---

## 8. SessionsView

**File**: `src/slices/identity/views/SessionsView.vue`
**Route**: `/account/sessions` (name: `identity.sessions`)
**Layout**: `AppShell`
**Auth**: required

### 8.1 Wireframe

```
┌──────────────────────────────────────────────────┐
│  Top Bar                                          │
├────────────┬─────────────────────────────────────┤
│            │                                     │
│  Sidebar   │  Active Sessions                    │
│            │  ─────────────────────────           │
│            │                                     │
│            │  ┌─────────────────────────────┐    │
│            │  │                             │    │
│            │  │  Chrome on Windows          │    │
│            │  │  192.168.1.42               │    │
│            │  │  Last used: 2 min ago       │    │
│            │  │  [current]      [Revoke]    │    │
│            │  │                             │    │
│            │  ├─────────────────────────────┤    │
│            │  │                             │    │
│            │  │  Firefox on macOS           │    │
│            │  │  10.0.0.15                  │    │
│            │  │  Last used: 3 hours ago     │    │
│            │  │               [Revoke]      │    │
│            │  │                             │    │
│            │  ├─────────────────────────────┤    │
│            │  │                             │    │
│            │  │  Mobile Safari on iOS       │    │
│            │  │  172.16.0.8                 │    │
│            │  │  Last used: 2 days ago      │    │
│            │  │               [Revoke]      │    │
│            │  │                             │    │
│            │  └─────────────────────────────┘    │
│            │                                     │
├────────────┴─────────────────────────────────────┤
```

### 8.2 Fields

No form fields. This is a list/action page.

### 8.3 Behavior

**On mount**:
1. Call `GET /api/auth/sessions` to fetch `SessionOut[]`.
2. Identify the current session by matching the session ID stored in the local auth state.
3. Sort sessions: current session first, then by `last_used_at` descending.

**Session card content** (per `SessionOut`):

| Field | Display | Format |
|-------|---------|--------|
| `user_agent` | Parsed into browser + OS (e.g., "Chrome on Windows") | Use a lightweight UA parser or display raw if parsing fails |
| `ip_inet` | IP address, `--color-muted`, 12px | As-is from server |
| `last_used_at` | Relative time (e.g., "2 minutes ago") | Use `useTimeAgo` composable or `Intl.RelativeTimeFormat` |
| `created_at` | Tooltip on the session card: "Created: {date}" | `Intl.DateTimeFormat` with date+time |
| Current badge | `SStatusBadge` with `$t('identity.sessions.currentBadge')` | Only on the current session |

**Revoke action**:
1. Revoke button is hidden on the current session (cannot revoke yourself).
2. Click triggers `useConfirmDialog().confirm()` with variant `warning`: `$t('identity.sessions.revokeConfirm')` -- "This will immediately sign out that device."
3. On confirm: call `DELETE /api/auth/sessions/{session_id}`.
4. On `204`: remove the session from the local list. Show `useToast().success()`: `$t('identity.sessions.revokeSuccess')`.

### 8.4 Error Handling

**Load errors**:

| Condition | Display | Recovery |
|-----------|---------|----------|
| Network / 5xx on fetch | `SAlert` variant `error`: `$t('identity.sessions.loadError')` with "Retry" button | Retry button calls fetch again |
| 401 (session expired) | Global interceptor redirects to login | -- |

**Revoke errors**:

| Condition | Display |
|-----------|---------|
| 404 (session already revoked) | `useToast().warning()`: `$t('identity.sessions.alreadyRevoked')`. Remove from list |
| Network / 5xx | `useToast().error()`: `$t('identity.sessions.revokeError')` |

### 8.5 Loading & Success States

| State | Visual |
|-------|--------|
| Loading | Three `SSkeleton` rows (card-shaped, height 80px each) inside the card container |
| Loaded (has sessions) | List of session items |
| Loaded (empty) | `SEmptyState` with `ComputerDesktopIcon`: `$t('identity.sessions.empty')` -- should not occur (current session always exists) |
| Load error | `SAlert` error with retry button |
| Revoking | The specific session's revoke button shows `SLoadingSpinner` inline, disabled |

### 8.6 Responsive Behavior

| Breakpoint | Behavior |
|------------|----------|
| >= 768px | Card max-width 640px, left-aligned within content area |
| < 768px | Card full-width, session items stack vertically, revoke button below metadata |

**Session item layout**:
- Desktop: flexbox row. Left: UA + IP + last-used stacked vertically. Right: badge + revoke button.
- Mobile (< 768px): full-width stack. Metadata on top, badge + revoke button below as a row.

### 8.7 Accessibility

- Session list: `<ul role="list">` with `<li>` per session.
- Current session: badge has `aria-label="$t('identity.sessions.currentLabel')"` for screen readers.
- Revoke button: `aria-label="$t('identity.sessions.revokeLabel', { device })"` including the device name for context.
- Confirmation dialog: managed by `SConfirmDialog` which handles focus trap, escape key, and ARIA automatically.
- Loading state: `aria-busy="true"` on the list container.
- Last-used time: `<time datetime="...">` with the ISO timestamp.

### 8.8 Components Used

| Component | Usage |
|-----------|-------|
| `SPageHeader` | Page title "Active Sessions" |
| `SCard` | Container for session list |
| `SStatusBadge` | "Current" badge on active session |
| `SButton` | Revoke button per session (variant: `danger`, size: `sm`) |
| `SConfirmDialog` | Revoke confirmation (via `useConfirmDialog`) |
| `SLoadingSpinner` | Inline in revoking button |
| `SSkeleton` | Loading placeholder |
| `SEmptyState` | Empty state (edge case) |
| `SAlert` | Load error display |
| `ComputerDesktopIcon` | Empty state icon (from `@heroicons/vue/24/outline`) |

---

## 9. DeleteAccountView

**File**: `src/slices/identity/views/DeleteAccountView.vue`
**Route**: `/account/delete` (name: `identity.deleteAccount`)
**Layout**: `AppShell`
**Auth**: required

### 9.1 Wireframe

```
┌──────────────────────────────────────────────────┐
│  Top Bar                                          │
├────────────┬─────────────────────────────────────┤
│            │                                     │
│  Sidebar   │  Delete Account                     │
│            │  ─────────────────────────           │
│            │                                     │
│            │  ┌─────────────────────────────┐    │
│            │  │                             │    │
│            │  │  [!] This action is         │    │
│            │  │  permanent and cannot be    │    │
│            │  │  undone. All your data      │    │
│            │  │  will be permanently        │    │
│            │  │  deleted.                   │    │
│            │  │                             │    │
│            │  │  Password                   │    │
│            │  │  ┌───────────────────┐      │    │
│            │  │  │             [eye] │      │    │
│            │  │  └───────────────────┘      │    │
│            │  │  Confirm your identity      │    │
│            │  │                             │    │
│            │  │  [ ] I understand this      │    │
│            │  │  action is permanent        │    │
│            │  │                             │    │
│            │  │  [ Delete My Account ]      │    │
│            │  │                             │    │
│            │  └─────────────────────────────┘    │
│            │                                     │
├────────────┴─────────────────────────────────────┤
```

### 9.2 Fields

| Field | Component | Type | Autocomplete | Required | Constraints | i18n Label |
|-------|-----------|------|--------------|----------|-------------|------------|
| `password` | `SInput` | `password` | `current-password` | yes | max 1024 | `identity.deleteAccount.password` |
| `confirmed` | `SCheckbox` | `checkbox` | -- | yes | must be checked | `identity.deleteAccount.confirm` |

### 9.3 Behavior

**On mount**: do not auto-focus any field (deliberate friction for destructive action).

**Warning banner**: `SAlert` variant `error` at the top of the card, with `ExclamationTriangleIcon`. Text: `$t('identity.deleteAccount.warning')`. This is always visible and not dismissible.

**Submit button**: variant `danger`, only enabled when both `password` is non-empty and `confirmed` is checked.

**On submit**:
1. Show `useConfirmDialog().confirm()` with variant `error`: `$t('identity.deleteAccount.finalConfirm')` -- "Are you sure? This cannot be undone." Confirm button text: `$t('identity.deleteAccount.finalConfirmButton')` -- "Delete permanently".
2. If confirmed: call `DELETE /api/auth/me` with `{ password }` in the request body.
3. On `204`: call `session.clear()`, redirect to `identity.login`.

### 9.4 Error Handling

| Error Slug / Status | HTTP | Display | Recovery |
|---------------------|------|---------|----------|
| `auth/invalid-credentials` | 401 | `SFormField` error on password: `$t('identity.errors.invalidCredentials')` | Clear password, refocus it |
| `tenancy/original-creator-self-delete-blocked` | 409 | `SAlert` error: `$t('identity.deleteAccount.blocked')` with list of org names from `extra.blocked_org_ids`. Message: "You must transfer ownership of these organizations before deleting your account: {orgNames}" | No retry; user must transfer ownership first |
| 429 (rate limit) | 429 | `SAlert` warning: `$t('identity.errors.rateLimit')` | Disable form, countdown |
| Network / 5xx | -- | `SAlert` error: `$t('identity.errors.generic')` | Enable retry |

**Blocked organization handling**: when the server returns `409` with `extra.blocked_org_ids`, the frontend should resolve those UUIDs to org names (from the session store or an additional API call) and list them in the error message. If resolution fails, display the UUIDs as fallback.

### 9.5 Loading & Success States

| State | Visual |
|-------|--------|
| Initial | Warning banner visible, form rendered, submit button disabled (checkbox unchecked) |
| Submitting | Submit button shows spinner + `$t('identity.deleteAccount.deleting')`, fields disabled |
| Confirm dialog | `SConfirmDialog` overlay with error variant |
| Success | `session.clear()`, redirect to `identity.login` |

### 9.6 Responsive Behavior

Same as ChangePasswordView: card max-width 480px, full-width below 768px.

### 9.7 Accessibility

- Warning banner: `role="alert"` (already handled by `SAlert`).
- Checkbox: associated label via `SFormField`, `aria-required="true"`.
- Submit button: `aria-disabled="true"` when conditions not met (not just `disabled` attribute, so screen readers announce the state).
- Confirmation dialog: focus trap, escape key, ARIA managed by `SConfirmDialog`.
- Destructive action emphasis: button text uses plain language, no ambiguity.

### 9.8 Components Used

| Component | Usage |
|-----------|-------|
| `SPageHeader` | Page title "Delete Account" |
| `SCard` | Form container |
| `SAlert` | Warning banner (variant: `error`, not dismissible), server error display |
| `SFormField` | Wraps password and checkbox |
| `SInput` | Password field |
| `SCheckbox` | Confirmation checkbox |
| `SButton` | Submit button (variant: `danger`, `loading` prop) |
| `SConfirmDialog` | Final confirmation (via `useConfirmDialog`, variant: `error`) |
| `ExclamationTriangleIcon` | Warning banner icon (from `@heroicons/vue/24/outline`) |

---

## 10. CaptchaWidget

**File**: `src/slices/identity/components/CaptchaWidget.vue`

This is a shared component used only within the identity slice (not in `src/shared/ui/`).

### 10.1 Props

| Prop | Type | Required | Description |
|------|------|----------|-------------|
| `provider` | `'hcaptcha' \| 'turnstile' \| 'off'` | yes | CAPTCHA provider |
| `sitekey` | `string` | yes | Site key for the provider |

### 10.2 Emits

| Event | Payload | Description |
|-------|---------|-------------|
| `update:token` | `string` | Emitted on solve (token string) or on expiry/error (empty string) |

### 10.3 Behavior

1. On mount: dynamically load the provider's script if not already loaded.
   - hCaptcha: `https://js.hcaptcha.com/1/api.js?render=explicit`
   - Turnstile: `https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit`
2. Render the widget into a `<div ref="container">` using the provider's `render()` API.
3. On solve: emit `update:token` with the token string.
4. On expiry or error: emit `update:token` with empty string.
5. On config change (provider or sitekey): destroy and re-render.
6. On unmount: destroy the widget and clean up.
7. If `provider` is `'off'`, render nothing.

### 10.4 Visual Spec

- Container: centered within the form, full card width.
- Loading: `SLoadingSpinner` with text `$t('identity.register.captchaLoading')` while the external script loads.
- The widget itself is styled by the provider (hCaptcha/Turnstile own the iframe appearance).
- Minimum height: 78px (to prevent layout shift during load).
- Dark mode: pass `theme: 'dark'` to the provider's render config when `data-theme="dark"` is active on `:root`.

### 10.5 Accessibility

- Container has `aria-label="$t('identity.register.captchaLabel')"` ("Security verification").
- The provider iframes handle their own internal accessibility.
- If the widget fails to load, show fallback text: `$t('identity.register.captchaFallback')` -- "Security check unavailable. Please try again later."

---

## 11. Shared Patterns

### 11.1 Password Visibility Toggle

All password `SInput` fields include a visibility toggle button:
- Icon: `EyeIcon` (hidden state) / `EyeSlashIcon` (visible state) from `@heroicons/vue/24/outline`.
- Clicking toggles the input `type` between `password` and `text`.
- Button: icon-only, variant `ghost`, inside the input's trailing slot.
- `aria-label`: `$t('identity.common.showPassword')` / `$t('identity.common.hidePassword')`.
- Touch target: 44x44px.

This behavior is built into `SInput` when `type="password"` -- the component renders the toggle automatically.

### 11.2 Password Policy Display

Views that accept new passwords (RegisterView, PasswordResetConfirmView, ChangePasswordView) display the password requirements as help text below the field:

```
At least 10 characters with a letter, digit, and symbol.
```

Text: `$t('identity.common.passwordPolicy')`, rendered at 12px in `--color-muted`. Connected to the input via `SFormField`'s `help` prop, which sets `aria-describedby` on the input.

### 11.3 AuthLayout Footer Links

Auth pages (in AuthLayout) show navigation links below the card:

| Page | Footer Text | Link |
|------|-------------|------|
| Register | "Already have an account? **Log in**" | `identity.login` |
| Login | "Don't have an account? **Register**" | `identity.register` |
| Password Reset Request | "**Back to Log In**" | `identity.login` |
| Password Reset Confirm | (none) | -- |
| Verify Email | (none) | -- |

Footer text: `--color-muted` at 14px, centered. Links: `--color-accent`, underline on hover.

### 11.4 Account Settings Navigation

The four authenticated account views (ChangePassword, ChangeEmail, Sessions, DeleteAccount) share a common navigation context. They are accessible from:

1. **Sidebar**: under the user menu or account section.
2. **UserMenu dropdown**: "Account Settings" navigates to `/account/password`.
3. **In-page**: no sub-navigation tabs between account views. Each is accessed independently via sidebar or user menu.

### 11.5 Flash Messages on Login Page

The LoginView reads query parameters to display one-time flash messages:

| Query Param | Alert Variant | i18n Key |
|-------------|---------------|----------|
| `pendingVerify=1` | `info` | `identity.login.pendingVerify` |
| `passwordReset=1` | `success` | `identity.login.passwordResetSuccess` |
| `passwordChanged=1` | `success` | `identity.login.passwordChangedSuccess` |

After displaying, the query parameter is consumed by replacing the current route (removing the param) to prevent showing the flash on page refresh.

### 11.6 Token-in-Fragment Pattern (SEC-8)

VerifyEmailView and PasswordResetConfirmView extract tokens from the URL fragment hash (`#token=...`) instead of query parameters. This is a deliberate security measure:

- Fragments are not sent to the server in HTTP requests.
- Fragments are not included in `Referer` headers.
- Fragments are not logged by proxies or server access logs.

Implementation: `new URLSearchParams(window.location.hash.slice(1)).get('token')`.

### 11.7 Rate Limit Handling

When any endpoint returns HTTP 429:

1. Parse the `Retry-After` response header (value in seconds).
2. Display a countdown timer in the error message: "Try again in {seconds}s."
3. Disable the form's submit button until the countdown reaches zero.
4. Use `setInterval` with 1-second ticks. Clear the interval on component unmount.
5. When countdown reaches zero, re-enable the form and clear the error message.

This pattern applies to all identity views that submit forms.

---

## 12. i18n Key Map

All user-facing strings are accessed via `$t()`. Keys are organized under the `identity` namespace.

### 12.1 Register

| Key | English Text |
|-----|-------------|
| `identity.register.title` | Create Account |
| `identity.register.email` | Email |
| `identity.register.password` | Password |
| `identity.register.passwordHelp` | At least 10 characters with a letter, digit, and symbol. |
| `identity.register.submit` | Create Account |
| `identity.register.submitting` | Creating account... |
| `identity.register.loginPrompt` | Already have an account? |
| `identity.register.loginLink` | Log in |
| `identity.register.captchaLoading` | Loading security check... |
| `identity.register.captchaLabel` | Security verification |
| `identity.register.captchaFallback` | Security check unavailable. Please try again later. |

### 12.2 Login

| Key | English Text |
|-----|-------------|
| `identity.login.title` | Log In |
| `identity.login.email` | Email |
| `identity.login.password` | Password |
| `identity.login.submit` | Log In |
| `identity.login.submitting` | Logging in... |
| `identity.login.forgot` | Forgot password? |
| `identity.login.registerPrompt` | Don't have an account? |
| `identity.login.registerLink` | Register |
| `identity.login.pendingVerify` | Check your email and click the verification link before logging in. |
| `identity.login.passwordResetSuccess` | Password reset successfully. Log in with your new password. |
| `identity.login.passwordChangedSuccess` | Password changed. Please log in with your new password. |

### 12.3 Verify Email

| Key | English Text |
|-----|-------------|
| `identity.verifyEmail.title` | Verify Email |
| `identity.verifyEmail.verifying` | Verifying your email... |
| `identity.verifyEmail.success` | Your email has been verified. |
| `identity.verifyEmail.continue` | Continue |
| `identity.verifyEmail.invalidToken` | This verification link is invalid. |
| `identity.verifyEmail.expiredToken` | This verification link has expired. Please register again. |
| `identity.verifyEmail.failure` | Verification failed. |

### 12.4 Password Reset

| Key | English Text |
|-----|-------------|
| `identity.passwordReset.requestTitle` | Reset Password |
| `identity.passwordReset.requestDescription` | Enter your email and we'll send a reset link. |
| `identity.passwordReset.email` | Email |
| `identity.passwordReset.requestSubmit` | Send Reset Link |
| `identity.passwordReset.sentTitle` | Check Your Email |
| `identity.passwordReset.sentDescription` | If an account with that email exists, we sent a password reset link. |
| `identity.passwordReset.confirmTitle` | Set New Password |
| `identity.passwordReset.newPassword` | New Password |
| `identity.passwordReset.confirmPassword` | Confirm Password |
| `identity.passwordReset.confirmSubmit` | Reset Password |
| `identity.passwordReset.invalidLink` | This reset link is invalid or missing. |
| `identity.passwordReset.invalidToken` | This reset link is invalid. |
| `identity.passwordReset.expiredToken` | This reset link has expired. |
| `identity.passwordReset.requestNewLink` | Request New Link |

### 12.5 Change Password

| Key | English Text |
|-----|-------------|
| `identity.changePassword.title` | Change Password |
| `identity.changePassword.current` | Current Password |
| `identity.changePassword.new` | New Password |
| `identity.changePassword.confirm` | Confirm New Password |
| `identity.changePassword.submit` | Change Password |
| `identity.changePassword.warning` | Changing your password will sign you out of all active sessions. |

### 12.6 Change Email

| Key | English Text |
|-----|-------------|
| `identity.changeEmail.title` | Change Email |
| `identity.changeEmail.currentLabel` | Current email |
| `identity.changeEmail.newEmail` | New Email |
| `identity.changeEmail.password` | Password |
| `identity.changeEmail.passwordHelp` | Confirm your identity |
| `identity.changeEmail.submit` | Update Email |
| `identity.changeEmail.sentTitle` | Verification Email Sent |
| `identity.changeEmail.sentDescription` | We sent a verification link to {email}. Click the link to confirm your new email. |

### 12.7 Sessions

| Key | English Text |
|-----|-------------|
| `identity.sessions.title` | Active Sessions |
| `identity.sessions.currentBadge` | Current |
| `identity.sessions.currentLabel` | This is your current session |
| `identity.sessions.revoke` | Revoke |
| `identity.sessions.revokeLabel` | Revoke session on {device} |
| `identity.sessions.revokeConfirm` | This will immediately sign out that device. |
| `identity.sessions.revokeSuccess` | Session revoked. |
| `identity.sessions.revokeError` | Failed to revoke session. |
| `identity.sessions.alreadyRevoked` | Session was already revoked. |
| `identity.sessions.loadError` | Failed to load sessions. |
| `identity.sessions.retry` | Retry |
| `identity.sessions.empty` | No other sessions found. |
| `identity.sessions.lastUsed` | Last used {time} |
| `identity.sessions.created` | Created {date} |

### 12.8 Delete Account

| Key | English Text |
|-----|-------------|
| `identity.deleteAccount.title` | Delete Account |
| `identity.deleteAccount.warning` | This action is permanent and cannot be undone. All your data, including organizations you created, projects, agents, and conversations will be permanently deleted. |
| `identity.deleteAccount.password` | Password |
| `identity.deleteAccount.passwordHelp` | Confirm your identity |
| `identity.deleteAccount.confirm` | I understand this action is permanent |
| `identity.deleteAccount.submit` | Delete My Account |
| `identity.deleteAccount.deleting` | Deleting account... |
| `identity.deleteAccount.finalConfirm` | Are you sure? This cannot be undone. |
| `identity.deleteAccount.finalConfirmButton` | Delete permanently |
| `identity.deleteAccount.blocked` | You must transfer ownership of the following organizations before deleting your account: |

### 12.9 Shared / Errors

| Key | English Text |
|-----|-------------|
| `identity.common.showPassword` | Show password |
| `identity.common.hidePassword` | Hide password |
| `identity.common.passwordPolicy` | At least 10 characters with a letter, digit, and symbol. |
| `identity.common.backToLogin` | Back to Log In |
| `identity.errors.generic` | Something went wrong. Please try again. |
| `identity.errors.invalidCredentials` | Incorrect email or password. |
| `identity.errors.emailTaken` | An account with this email already exists. |
| `identity.errors.domainDenied` | This email domain is not allowed. |
| `identity.errors.emailInvalid` | Please enter a valid email address. |
| `identity.errors.weakPassword` | Password does not meet the requirements. |
| `identity.errors.captchaRequired` | Please complete the security check. |
| `identity.errors.emailUnverified` | Please verify your email before logging in. |
| `identity.errors.lockout` | Too many failed attempts. Try again in {seconds}s. |
| `identity.errors.rateLimit` | Too many requests. Please try again later. |
| `identity.errors.resetRateLimit` | Too many reset requests. Please try again later. |
| `identity.errors.banned` | This account has been suspended. |
| `identity.errors.accountDeleted` | This account has been deleted. |
| `identity.validation.emailRequired` | Email is required. |
| `identity.validation.emailFormat` | Please enter a valid email address. |
| `identity.validation.passwordRequired` | Password is required. |
| `identity.validation.passwordMinLength` | Password must be at least 10 characters. |
| `identity.validation.passwordMismatch` | Passwords do not match. |
| `identity.validation.passwordSame` | New password must be different from current password. |
| `identity.validation.emailSame` | New email must be different from current email. |

---

## 13. Files Summary

### Views to Rewrite

All 9 existing view files are rewritten in place. No new routes are added.

| File | Layout | Key Changes |
|------|--------|-------------|
| `src/slices/identity/views/RegisterView.vue` | AuthLayout | Add SFormField, SInput, SButton, SAlert; password help text; field-level validation |
| `src/slices/identity/views/LoginView.vue` | AuthLayout | Add flash messages, lockout countdown, SFormField, SInput, SButton, SAlert |
| `src/slices/identity/views/VerifyEmailView.vue` | AuthLayout | Three-state card (verifying/success/failure) with icons |
| `src/slices/identity/views/PasswordResetRequestView.vue` | AuthLayout | Two-state card (form/sent) with envelope icon |
| `src/slices/identity/views/PasswordResetConfirmView.vue` | AuthLayout | Add confirm password field, token error state, SAlert |
| `src/slices/identity/views/ChangePasswordView.vue` | AppShell | Wrap in SPageHeader + SCard, add confirm field, session warning |
| `src/slices/identity/views/ChangeEmailView.vue` | AppShell | Wrap in SPageHeader + SCard, show current email, two-state card |
| `src/slices/identity/views/SessionsView.vue` | AppShell | Wrap in SPageHeader + SCard, session list with badges, confirm dialog |
| `src/slices/identity/views/DeleteAccountView.vue` | AppShell | Wrap in SPageHeader + SCard, warning banner, confirm dialog, blocked orgs list |

### Components Modified

| File | Changes |
|------|---------|
| `src/slices/identity/components/CaptchaWidget.vue` | Add loading spinner, dark mode support, fallback text, aria-label |

### Route Meta Updates

| File | Changes |
|------|---------|
| `src/slices/identity/routes.ts` | Add `layout: 'auth'` meta to public routes; `layout: 'app'` is default for `requiresAuth` routes |

### i18n Updates

| File | Changes |
|------|---------|
| `src/slices/identity/locales/en.json` | Add all keys from Section 12 |

### Dependencies on Design System (from 01-design-system.md)

These shared components must exist before identity views can be rewritten:

| Component | Used By |
|-----------|---------|
| `SButton` | All 9 views |
| `SInput` | 7 views (all with form fields) |
| `SFormField` | 7 views |
| `SCard` | 4 views (AppShell account views) |
| `SAlert` | 8 views |
| `SPageHeader` | 4 views (AppShell account views) |
| `SCheckbox` | DeleteAccountView |
| `SSkeleton` | SessionsView |
| `SStatusBadge` | SessionsView |
| `SEmptyState` | SessionsView |
| `SConfirmDialog` | SessionsView, DeleteAccountView (already exists) |
| `SLoadingSpinner` | RegisterView, VerifyEmailView, SessionsView (already exists) |

### Dependencies on Layout Shell (from 02-layout-shell.md)

| Component | Required For |
|-----------|-------------|
| `AuthLayout` | 5 public auth views |
| `AppShell` | 4 authenticated account views |
