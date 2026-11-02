# Security audit: login, sign-up and sessions

An audit of how people sign in to Dastango, checked against the OWASP
Application Security Verification Standard (ASVS) **4.0.3**, chapter 2
(authentication) and chapter 3 (session management), at levels 1 and 2.
Ticket DST-21. Audit only: no code was changed.

The product's overall security posture, threat model and how to report a
problem are in the root [SECURITY.md](../SECURITY.md). This file is the audit.

| | |
|---|---|
| Date | 30 October 2026 |
| Code audited | `main` at `8ca039f` (after DST-16) |
| Standard | [OWASP ASVS 4.0.3](https://github.com/OWASP/ASVS/tree/v4.0.3), CC BY-SA 4.0. Requirements below are paraphrased, not quoted: read the standard for the exact wording. |
| Scope | The 71 requirements of V2 and V3 that apply at level 1 or 2 (V2: 53, V3: 18). Rows of the DST-21 checklist that the standard puts in other chapters are cross-referenced in sections 4 and 5. |
| Not assessed | V2.10 (service authentication: how the server logs in to Postgres and third parties). It is not login or sign-up. |
| ASVS 5.0 | The current edition (May 2025) renumbers these chapters as V6 and V7. The ticket asks for "chapters 2 and 3", so 4.0.3 is used. A 5.0 mapping is not done here. |

## 1. Result

| Set | Requirements | Pass | Fail | N/A | Not assessed |
|---|---:|---:|---:|---:|---:|
| Level 1 | 39 | 21 | 10 | 8 | 0 |
| Added at level 2 | 32 | 5 | 3 | 20 | 4 |
| **Both levels** | **71** | **25** | **14** | **28** | **4** |

Requirement 3.3.2 sets a different limit at each level. It passes at level 1
(sessions last 14 days; the limit is 30) and fails at level 2 (12 hours, or 30
minutes idle). It is counted as a pass in the level 1 row and as one of the 14
failures overall.

**After DST-23 (2 November 2026):** 2.2.1 moves from fail to pass. Level 1 is
22 pass, 9 fail, 8 N/A; both levels are 26 pass, 13 fail, 28 N/A, 4 not
assessed.

**After DST-24 (2 November 2026):** 2.1.1 and 2.1.2 move from fail to pass.
2.1.7 is fixed at sign-up and password change but not at sign-in, so it stays a
fail. Level 1 is 24 pass, 7 fail, 8 N/A; both levels are 28 pass, 11 fail, 28
N/A, 4 not assessed. The table above is the audit as run on 30 October.

**What is solid:** passwords are stored with argon2id and a unique salt;
session tokens are 256-bit random values, stored only as a SHA-256 digest, in
an `HttpOnly`, `SameSite=Lax` cookie that is `Secure` in production; a new
token is issued at every sign-in; sign-out, password change and disabling an
account end sessions at once; nothing puts a token in a URL.

**What fails, in the order it matters** (each has a draft ticket in section 7):

1. Nothing limits sign-in or sign-up attempts per IP address, and anyone who
   knows an address can lock that account for 15 minutes (2.2.1).
   **Update, 2 November 2026 (DST-23): a limit per address now exists. The lock
   can still be caused, but only a couple of accounts per address per 15
   minutes (section 8).**
2. The password rules are weaker than the standard: 10 characters where it asks
   for 12, up to 1,024 where it asks for at most 128, and no check against
   breached passwords (2.1.1, 2.1.2, 2.1.7). The sign-in page has no strength
   meter and no way to reveal the typed password (2.1.8, 2.1.12).
   **Update (DST-24): the length rules and the breached-password check are
   done at sign-up and password change (section 8). Not done: the check at
   sign-in, the strength meter and the reveal control.**
3. A locked or disabled account answers differently from a wrong password, and
   sign-up says when an address is taken. The root `SECURITY.md` claimed
   identical answers; that was true only for active, unlocked accounts and has
   been corrected (finding F-a).
   **Update (DST-23): sign-in now answers identically for all four cases;
   sign-up still says an address is taken (section 8).**
4. The product sends no email at all, so there is no password reset, no
   notification of a password change, and no email verification (2.2.3, 2.5.5,
   2.5.6).
5. Sessions can be listed but not ended one by one (3.3.4), have no idle limit
   (3.3.2 at level 2), and the cookie has no `__Host-` prefix (3.4.4).
6. There is no CSRF token, double-submit cookie or `Origin` check (4.2.2,
   13.2.3). `SameSite=Lax`, JSON-only bodies and no CORS are what protect today.
7. Level 2 only: no second factor (2.3.2) and no server-side secret in password
   hashing (2.4.5).

## 2. How it was checked

1. **Code.** Every file that handles sign-up, sign-in, passwords, sessions and
   the cookie was read: `auth/accounts.py`, `auth/sessions.py`,
   `auth/passwords.py`, `auth/deps.py`, `auth/github.py`,
   `backend/routes/auth.py`, `backend/app.py`, and the sign-in page
   `web/components/AuthGate.tsx`. Production settings were read from
   `deploy/Caddyfile`, `deploy/docker-compose.prod.yml` and
   `deploy/setup-vm.sh`.
2. **Probes** (section 3) against a throwaway local instance: a fresh data
   folder (`DATA_DIR`), no `.env`, no provider keys, sign-ups opened, port 8095,
   role-based test accounts (`admin01@example.test`, `user01@example.test`,
   and so on). They deliberately lock accounts and fire failed logins, so they
   were **never** pointed at the live site or any real deployment.
3. **Existing tests.** `tests/unit/test_m5_auth.py` and
   `tests/unit/test_m8_github_signin.py` are cited where they already prove a
   row.

The probe script and its log are kept locally under `data/benchmarks/`
(ignored by git, session tokens removed from the log). They are not committed
because this ticket changes no code.

**Limits.** Black-box probes on one instance, run once. The sign-in page was
read, not driven in a browser. TLS, HSTS and the proxy were checked from their
configuration files, not by attacking the live server. Dependencies were not
audited here (see F11 in [REQUEST_TRACE.md](REQUEST_TRACE.md)).

## 3. Probe results

All on 30 October 2026, Windows, Python 3.11, locked dependencies.

| # | What was done | Result |
|---|---|---|
| P1 | First sign-up, then read the cookie | The first account became admin. `Set-Cookie: storygen_session=<43 characters>; HttpOnly; Max-Age=1209600; Path=/; SameSite=lax`. 43 characters of URL-safe base64 is 32 random bytes, 256 bits. Over plain HTTP there is no `Secure`, by design. |
| P2 | Signed in twice as the same user | Two different tokens. The first token still worked after the second sign-in (200). The user then had 3 live sessions. |
| P3 | Failed logins: unknown email, wrong password, 8 wrong guesses, correct password while locked, disabled account. Timing is the median, then min to max, of 5 requests in ms | Unknown email and wrong password: both `401 "email or password is incorrect"`, 74 ms (67 to 89) against 70 ms (68 to 89). Locked: `"too many failed attempts, try again in 15 minute(s)"`, also for the **correct** password, 14 ms (3 to 28). Disabled: `"that account is disabled"`, 15 ms (3 to 16). |
| P4 | Signed up with an address already registered | `400 "that email address is already registered"`. |
| P5 | Registered with 16 different passwords | Rejected: 9 characters, 10 spaces, 1,025 characters. Accepted: 10, 11, 12, 64, 128, **129** and **1,024** characters, `1234567890`, `password12`, `qwertyuiop`, `iloveyou123`, an emoji password, and `a   b   c   d` (13 characters as typed). A 96-character password was not truncated: a different tail after character 72 was refused. |
| P6 | 40 failed logins for 40 different emails from one IP; 15 sign-ups from one IP | All 40 answered `401` in 3.0 s. All 15 sign-ups succeeded in 1.2 s. No `429`. |
| P7 | Sign out | The cookie was cleared (`Max-Age=0`) and the old token then got `401`. |
| P8 | Changed the password | `200`, a new token was issued, the token used for the change got `401` afterwards and the new one worked. A wrong current password got `403`. (That the user's *other* sessions end is proven by `test_changing_a_password_ends_every_other_session`, not by this probe.) |
| P9 | Listed `/api/auth` endpoints from the OpenAPI document | `GET /sessions` exists. There is no endpoint to end a session, and none for reset, TOTP, verification or recovery. |
| P10 | Cross-site style requests | Sign-out with `Origin: https://evil.example` and a form body: `200`, session ended. Password change with a `text/plain` or form body: `422`, the handler is not reached. CORS preflight from a foreign origin: `405`, no `Access-Control-Allow-Origin`. |
| P11 | Session lifetime | 14 days from creation to expiry, fixed. `last_seen_at` is recorded but never enforced. |
| P13 | How passwords are stored | `argon2id`, `v=19`, `m=65536,t=3,p=4` (64 MiB, 3 passes, 4 lanes), 16-byte salt, 32-byte hash. The same password hashed twice gives different hashes. |
| P18 | Admin actions | Promoting a user to admin and creating an account with an admin-chosen password both worked with only the admin's session cookie. |
| P-HTTPS | Login with `X-Forwarded-Proto: https` | The cookie gained `Secure`. Production sets `FORWARDED_ALLOW_IPS="*"` behind Caddy and `deploy/setup-vm.sh` writes `COOKIE_SECURE=1`. |

## 4. The DST-21 checklist, row by row

| Guardrail | Before the audit | Result | ASVS | Where |
|---|---|---|---|---|
| Passwords hashed with argon2 | in place | **Pass** | 2.4.1, 2.4.2 | `auth/passwords.py`; P13. (2.4.5, a server-side secret, fails at level 2.) |
| Lockout after repeated failures | in place | **Pass, with a flaw** | 2.2.1 | `auth/accounts.py` `authenticate`; lets anyone lock a known account (P3). Since DST-23 one address can do that to a couple of accounts per 15 minutes, not more (section 8); it is bounded, not removed (F-d). |
| Logout and password change end sessions | in place, tested | **Pass** | 3.3.1, 3.3.3 | `sessions.revoke`, `revoke_all`; tests `test_signing_out_makes_the_cookie_useless`, `test_changing_a_password_ends_every_other_session`; P7, P8 |
| Sign-ups closed by default | in place | **N/A** | none in V2 or V3 | `accounts.signups_allowed`; test `test_signups_close_after_the_first_account_unless_opened`. A deployment choice, not an ASVS requirement. See F-b. |
| Owner checks on films and assets | in place | **Pass** (other chapter) | 4.2.1 | `auth/deps.py` `require_project`, `/assets` route; tests `test_another_account_cannot_*` |
| CORS closed unless origins are named | in place | **Pass** (other chapter) | 14.5.3 | `backend/app.py` adds CORS only when `CORS_ORIGINS` is set; P10 |
| Rate limit on login and sign-up by IP | to check | **Pass since DST-23** (was Fail) | 2.2.1 | `auth/ratelimit.py`; `tests/unit/test_login_rate_limit.py`; P6 repeated on 2 Nov 2026 (section 8) |
| Same error for wrong password and unknown user | to check | **Pass for sign-in since DST-23** (was: fail for locked and disabled accounts) | none in V2 or V3 | P3 repeated on 2 Nov 2026 (section 8). Sign-up still says "already registered" (P4), accepted until email verification (T7). See F-a. |
| Password rules: length, breached passwords | to check | **Pass for length since DST-24; breached passwords fixed at sign-up and change, not at sign-in** (was Fail) | 2.1.1, 2.1.2, 2.1.7 | `auth/passwords.py`; P5 repeated on 2 Nov 2026 (section 8) |
| Cookie flags; session renewed at login | to check | **Pass**, except the `__Host-` prefix | 3.4.1 to 3.4.3, 3.2.1, **3.4.4 fails** | `auth/sessions.py` `cookie_kwargs`; P1, P2, P-HTTPS |
| CSRF protection on state-changing requests | to check | **Fail** (other chapter) | 4.2.2, 13.2.3 | P10; compensating controls named in section 5 |
| Email verification on sign-up | to check | **Fail** (not an ASVS V2 or V3 requirement) | related: 2.2.3, 2.5.5 | Not built; the product has no email. See F-a. |
| Password reset | missing | **Fail** | 2.5.6 | P9 |
| List and revoke sessions per device | missing | **Fail** (listing exists, revoking does not) | 3.3.4 | `backend/routes/auth.py` `my_sessions`; P9 |
| Two-factor login (TOTP) | missing | **N/A** under V2 until it exists; the nearest requirement fails | 2.3.2 (level 2), 2.8.x apply once OTP is offered | P9 |

## 5. Every requirement

Verdicts are **Pass**, **Fail**, **N/A** (the feature the requirement is about
does not exist) or **Not assessed** (out of scope). Evidence names the file,
function, test or probe.

### V2 Authentication

| ASVS | Lvl | Requirement (paraphrased) | Verdict | Evidence |
|---|---|---|---|---|
| 2.1.1 | 1 | Passwords are at least 12 characters, with runs of spaces counted once | **Pass since DST-24** (was Fail) | Minimum 12 with each run of spaces counted once (`MIN_PASSWORD_LENGTH`, `effective_length` in `auth/passwords.py`). P5 repeated on 2 Nov 2026: 9, 10 and 11 characters and `a   b   c   d` refused, 12 accepted (section 8) |
| 2.1.2 | 1 | 64-character passwords are allowed; more than 128 are refused | **Pass since DST-24** (was Fail) | 64 and 128 accepted; 129, 1,024 and 1,025 refused with a message (`MAX_PASSWORD_LENGTH` 128 applies when a password is chosen; sign-in still accepts up to 1,024 so older accounts work) (P5 repeated, section 8) |
| 2.1.3 | 1 | Passwords are not truncated | **Pass** | Different tail after character 72 refused (P5) |
| 2.1.4 | 1 | Any printable Unicode is allowed | **Pass** | Emoji password accepted (P5) |
| 2.1.5 | 1 | Users can change their password | **Pass** | `change_password` in `backend/routes/auth.py` |
| 2.1.6 | 1 | Changing it needs the current and the new password | **Pass** | `PasswordChange`; P8; test `test_the_wrong_current_password_cannot_change_the_password` |
| 2.1.7 | 1 | Passwords are checked against a breached list at sign-up, sign-in and change | **Fail, partly fixed (DST-24)** | Sign-up, password change, admin-created accounts and the command line refuse a password on the bundled list of 29,954 (`auth/common_passwords.txt`, no outside service). Sign-in does not check, so an existing account with a listed password still signs in until it changes it (section 8) |
| 2.1.8 | 1 | A password strength meter is shown | **Fail** | `AuthGate.tsx` shows only the minimum length |
| 2.1.9 | 1 | No composition rules | **Pass** | Only length, not-all-whitespace and the breach list (`check_strength`); no capitals, digits or symbols are demanded |
| 2.1.10 | 1 | No forced rotation or password history | **Pass** | Neither exists |
| 2.1.11 | 1 | Paste, browser helpers and password managers work | **Pass** | No paste handler; `autoComplete` is `new-password` or `current-password` (`AuthGate.tsx`) |
| 2.1.12 | 1 | The typed password can be revealed temporarily | **Fail** | The field is always masked |
| 2.2.1 | 1 | Anti-automation stops credential stuffing and brute force | **Pass since DST-23** (was Fail) | Per-account lock (8 failures, 15 minutes) plus, since DST-23, a per-address limit on sign-in (20 failures in 15 minutes) and sign-up (10 attempts an hour), stored in the database. On 30 Oct, 40 failed logins and 15 sign-ups from one IP were unthrottled (P6); on 2 Nov the same probe got 20 answers then 429 (section 8). One address can still lock a known account, but only a couple per window (F-d) |
| 2.2.2 | 1 | Weak authenticators (SMS, email) only as a secondary check | **N/A** | None offered |
| 2.2.3 | 1 | Users are notified of changes to their sign-in details | **Fail** | The product sends no email or message |
| 2.3.1 | 1 | System-generated initial secrets are random, short-lived, not permanent | **N/A** | The system generates none. An admin-created account gets an admin-chosen password with no forced change (P18) |
| 2.3.2 | 2 | User-provided authenticators (FIDO, U2F) are supported | **Fail** | Passwords and GitHub only |
| 2.3.3 | 2 | Time-bound authenticators can be renewed | **N/A** | None exist |
| 2.4.1 | 2 | Passwords stored salted and hashed with an approved function | **Pass** | argon2id (`auth/passwords.py`); P13 |
| 2.4.2 | 2 | Unique salt of at least 32 bits | **Pass** | 16-byte salt, different hash each time (P13) |
| 2.4.3 | 2 | PBKDF2 iteration count | **N/A** | Not used |
| 2.4.4 | 2 | bcrypt work factor | **N/A** | Not used |
| 2.4.5 | 2 | An extra keyed pass with a secret held only by the verifier | **Fail** | No server-side secret is applied |
| 2.5.1 | 1 | Recovery and activation secrets are not sent in clear text | **N/A** | None exist |
| 2.5.2 | 1 | No password hints or secret questions | **Pass** | None in the API or UI |
| 2.5.3 | 1 | Recovery never reveals the current password | **Pass** | Only hashes are stored and there is no recovery flow |
| 2.5.4 | 1 | No default or shared accounts | **Pass** | None built in. See F-b for the first account |
| 2.5.5 | 1 | The user is told when a sign-in factor changes | **Fail** | No notification channel exists |
| 2.5.6 | 1 | Forgotten-password recovery uses a secure mechanism | **Fail** | No user-facing recovery. Only an admin on the server can reset (`python main.py users passwd`) |
| 2.5.7 | 2 | Losing an OTP or MFA factor needs identity proofing | **N/A** | No such factors |
| 2.6.1 to 2.6.3 | 2 | Lookup secrets (recovery codes) | **N/A** | None offered |
| 2.7.1 to 2.7.4 | 1 | Out-of-band authenticators: not SMS by default, expire in 10 minutes, single use, secure channel | **N/A** | None offered |
| 2.7.5, 2.7.6 | 2 | Out-of-band codes stored hashed and generated randomly | **N/A** | None offered |
| 2.8.1 | 1 | Time-based OTPs expire | **N/A** | No OTP offered |
| 2.8.2 to 2.8.7 | 2 | OTP key protection, algorithms, single use, reuse logging, revocation, biometrics only as a secondary factor | **N/A** | No OTP or biometrics |
| 2.9.1 to 2.9.3 | 2 | Cryptographic authenticators | **N/A** | None offered |
| 2.10.1 to 2.10.4 | 2 | Service authentication: unchanging intra-service secrets, default service accounts, stored service passwords, secrets in source control | **Not assessed** | Out of scope. For 2.10.4 only: `.env` is gitignored and gitleaks finds nothing in history |

### V3 Session management

| ASVS | Lvl | Requirement (paraphrased) | Verdict | Evidence |
|---|---|---|---|---|
| 3.1.1 | 1 | Session tokens never appear in URLs | **Pass** | Cookie only; no `token=` anywhere in `web/`, `frontend/`, `backend/`; the WebSocket reads the cookie (`auth/deps.py` `websocket_user`) |
| 3.2.1 | 1 | A new session token is issued at sign-in | **Pass** | `_start_session` at register, login, password change and GitHub sign-in; two logins gave two tokens (P2) |
| 3.2.2 | 1 | Tokens have at least 64 bits of entropy | **Pass** | `secrets.token_urlsafe(32)`, 256 bits (P1) |
| 3.2.3 | 1 | Tokens are stored in the browser only in a secure cookie | **Pass** | `HttpOnly` cookie; no `localStorage` or `sessionStorage` use found in the web app |
| 3.2.4 | 2 | Tokens come from an approved cryptographic generator | **Pass** | Python `secrets` module (operating system random source) |
| 3.3.1 | 1 | Sign-out and expiry invalidate the session | **Pass** | `sessions.revoke`; expired rows deleted in `resolve`; P7; test `test_signing_out_makes_the_cookie_useless` |
| 3.3.2 | 1, 2 | Re-authentication after 30 days (level 1) or 12 hours / 30 minutes idle (level 2) | **Pass at level 1, fail at level 2** | Fixed 14-day lifetime (`SESSION_DAYS`); `last_seen_at` is recorded, never enforced (P11) |
| 3.3.3 | 2 | After a password change, other sessions can be ended | **Pass** | `change_password` always calls `sessions.revoke_all` (forced rather than optional); test `test_changing_a_password_ends_every_other_session`; P8 |
| 3.3.4 | 2 | Users can see, and after re-entering credentials end, any or all their sessions | **Fail** | Listing exists (`my_sessions`), ending one does not, and no password is asked (P9) |
| 3.4.1 | 1 | The session cookie is `Secure` | **Pass** | `cookie_kwargs`: `Secure` on HTTPS or when `COOKIE_SECURE=1`; P-HTTPS; `deploy/setup-vm.sh` writes `COOKIE_SECURE=1`. Plain-HTTP development cookies are not `Secure`, by design |
| 3.4.2 | 1 | The session cookie is `HttpOnly` | **Pass** | P1; test `test_the_session_cookie_is_httponly_and_the_token_is_not_stored` |
| 3.4.3 | 1 | The session cookie sets `SameSite` | **Pass** | `SameSite=lax` (P1) |
| 3.4.4 | 1 | The cookie name uses the `__Host-` prefix | **Fail** | Name is `storygen_session` (`COOKIE_NAME`) |
| 3.4.5 | 1 | The cookie path is as narrow as possible when other apps share the domain | **Pass** | No `Domain` attribute, so the cookie is host-only; the app is served at the root of its own host |
| 3.5.1 | 2 | Users can revoke OAuth tokens held by linked apps | **N/A** | The GitHub token is used once at sign-in and dropped (`github.exchange_code`, `fetch_user`); nothing is stored. (GitHub cannot yet be disconnected from the UI: a known gap in CLAUDE.md, not this requirement) |
| 3.5.2 | 2 | Session tokens, not static API secrets, identify users | **Pass** | Opaque per-session tokens; no per-user static keys |
| 3.5.3 | 2 | Stateless tokens are signed or encrypted | **N/A** | No stateless tokens: sessions are database rows |
| 3.7.1 | 1 | A full valid session, or re-authentication, before sensitive actions | **Pass** | Every state-changing route needs a session (`require_user`); changing the password also asks for the current one (P8). Admin actions need only the session (P18): see F-e |

### Other chapters, for the checklist

| ASVS | Requirement (paraphrased) | Verdict | Evidence |
|---|---|---|---|
| 4.2.1 | Records are protected against access to other people's data (IDOR) | **Pass** | `require_project` answers 404 for another user's project; tests `test_another_account_cannot_see_the_project`, `..._drive_the_project`, `..._download_the_film`, `..._see_or_cancel_the_job` |
| 4.2.2 | A strong anti-CSRF mechanism protects authenticated functions | **Fail** | No token, double-submit cookie or `Origin` check. Compensating controls: `SameSite=Lax` cookie; JSON endpoints refuse form and `text/plain` bodies with 422 (P10); no CORS. Sign-out needs no body and accepts a foreign `Origin` (P10), which is low impact |
| 13.2.3 | Cookie-based REST services use a CSRF nonce, a double-submit cookie or `Origin` checks | **Fail** | Same as 4.2.2 |
| 14.5.3 | CORS allows only a strict list of trusted origins | **Pass** | CORS is added only when `CORS_ORIGINS` names origins (`backend/app.py`); P10 |

## 6. Other findings

These are not failures of a V2 or V3 requirement, but the audit found them.

- **F-a: the product reveals which accounts exist.** A locked account answers
  `too many failed attempts…` and a disabled one `that account is disabled`,
  both different from an unknown email, and they return 5 times faster than a
  real check because they skip the password hash (14 and 15 ms against 70 ms,
  P3). Sign-up says `that email address is already registered` (P4). The
  standard has no V2 or V3 requirement on this, but the project had promised
  identical answers and timing, and that held only for active, unlocked
  accounts. The root `SECURITY.md` is corrected.
  **Update (DST-23): fixed for sign-in. A locked or disabled account now gets
  the wrong-password answer after the same work. Still open: sign-up's
  "already registered", and the GitHub sign-in path, which says "that account
  is disabled" (it is shown only after GitHub has shown the visitor owns the
  linked identity).**
- **F-b: the first visitor to a new deployment becomes its admin.**
  `/api/auth/status` is public and says `needs_setup` until an account exists
  (`accounts._insert` makes the first account an admin). That is the intended
  setup flow, and the live site already has accounts, but a freshly deployed
  server is open to whoever reaches it first.
- **F-c: sessions outlive a new sign-in.** Signing in again does not end the
  earlier session (P2), and until 3.3.4 is fixed none can be ended one by one.
- **F-d: lockout can be turned against its owner.** 8 wrong guesses lock any
  known address for 15 minutes, even against the correct password (P3).
  **Update (DST-23): bounded, not removed.** One address gets 20 failed
  logins per 15 minutes, so it can lock at most two accounts per window; many
  addresses can still lock more. The owner now sees the ordinary wrong-password
  message and has to wait 15 minutes or have an admin run
  `python main.py users passwd <email>`, which also clears the lock.
- **F-e: admin actions need only the session.** Promoting a user or creating an
  account with a chosen password asks for no password (P18). This passes 3.7.1
  as written; asking again is worth considering.
- **F-f: `/api/auth/status` is public.** It reveals whether sign-ups are open,
  whether setup is pending, the minimum password length and whether GitHub is
  enabled.

## 7. Draft tickets for Sprint 2

Drafts for review: **none has been created in Jira.** "Fail" means a failing
requirement above; "Finding" means section 6. Points are estimates.

| # | Ticket | From | Priority | Pts | Depends on |
|---|---|---|---|---:|---|
| T1 | Rate-limit sign-in and sign-up per IP address, and stop a stranger locking an account **(DST-23: the limit is done; the lock is bounded, not removed)** | 2.2.1, F-d | High | 3 | |
| T2 | Make locked and disabled accounts answer like any wrong password **(DST-23, done; sign-up's answer accepted until T7)** | F-a | High | 2 | |
| T3 | Bring password rules up to the standard: 12 characters, at most 128, no breached passwords **(DST-24, done; 2.1.7 stays open at sign-in)** | 2.1.1, 2.1.2, 2.1.7 | High | 2 | |
| T4 | Strength meter and show/hide on the sign-in page | 2.1.8, 2.1.12 | Medium | 1 | T3 |
| T5 | Send email through a free provider, and notify on password change | 2.2.3, 2.5.5 | Medium | 3 | |
| T6 | Reset a forgotten password by email | 2.5.6 | Medium | 3 | T5 |
| T7 | Verify the email address at sign-up | F-a | Medium | 2 | T5 |
| T8 | Let people see and end their sessions, one or all | 3.3.4, F-c | Medium | 2 | |
| T9 | Check `Origin` on state-changing requests | 4.2.2, 13.2.3 | Medium | 1 | |
| T10 | Idle and absolute session timeouts | 3.3.2 (level 2) | Medium | 2 | |
| T11 | Protect first-run admin setup with a one-time token | F-b | Medium | 2 | |
| T12 | Two-factor sign-in with an authenticator app (TOTP) | 2.3.2 | Medium | 5 | |
| T13 | Use the `__Host-` prefix for the session cookie | 3.4.4 | Low | 1 | |
| T14 | Add a server-side secret to password hashing | 2.4.5 | Low | 1 | |

Total: 30 points. T1 to T3 close the level 1 failures that matter most.

### T1: Rate-limit sign-in and sign-up per IP address, and stop a stranger locking an account (High, 3)
**Status: done in DST-23 (2 November 2026), except the second box, which is only bounded.** See section 8.
**Why:** 2.2.1. 40 failed logins for 40 emails and 15 sign-ups from one IP were
all answered at full speed (P6), and 8 wrong guesses lock any known account for
15 minutes (P3).
**Acceptance criteria**
- [ ] `/api/auth/login` and `/api/auth/register` answer `429` with `Retry-After` after a per-IP limit, and the real client address is used behind Caddy
- [ ] A lock no longer lets a stranger keep an account unusable (decision recorded: per IP and account, or growing delays)
- [ ] Someone signing in from another IP is not affected by an attacker's failures
- [ ] Tests for the limit, the reset, the proxy header and the legitimate user; limits written in `docs/SECURITY.md`

### T2: Make locked and disabled accounts answer like any wrong password (High, 2)
**Status: done in DST-23 (2 November 2026).** Sign-up's "already registered" is recorded as accepted until T7. See section 8.
**Why:** F-a. They answer differently and 5 times faster (P3), against the promise in `SECURITY.md`.
**Acceptance criteria**
- [ ] A locked or disabled account returns the same status and message as a wrong password, after the same amount of work
- [ ] The decision on telling a locked-out owner how to recover is recorded (it needs email, T5)
- [ ] Sign-up's "already registered" answer is either generic or recorded as accepted until T7
- [ ] Tests compare status, message and timing across unknown, wrong, locked and disabled

### T3: Bring password rules up to the standard (High, 2)
**Status: done in DST-24 (2 November 2026).** The local list was chosen over a lookup, existing accounts are not asked to change at sign-in, and the check at sign-in is left open. See section 8.
**Why:** 2.1.1, 2.1.2, 2.1.7. Minimum 10, maximum 1,024, and `1234567890` is accepted (P5).
**Acceptance criteria**
- [ ] At least 12 characters, with runs of spaces counted once; more than 128 refused; 64 allowed
- [ ] Passwords on a breached list are refused at sign-up and password change (decision recorded: local list or a k-anonymity range lookup, so the password never leaves the server in clear)
- [ ] Existing short passwords keep working and the owner is asked to change at next sign-in (decision recorded)
- [ ] `/status` and the sign-in page show the new minimum; tests cover every case in P5

### T4: Strength meter and show/hide on the sign-in page (Medium, 1)
**Why:** 2.1.8, 2.1.12.
**Acceptance criteria**
- [ ] A strength indication while typing a new password, using the same rules as T3, with no external call
- [ ] A control to reveal the typed password temporarily, reachable by keyboard
- [ ] Web typecheck and build pass

### T5: Send email through a free provider, and notify on password change (Medium, 3)
**Why:** 2.2.3, 2.5.5. The product cannot tell an owner anything.
**Acceptance criteria**
- [ ] A free-tier provider is chosen and recorded (the project is at $0); credentials live in `.env` only
- [ ] A message is sent when a password changes or GitHub is connected; a send failure never blocks the action
- [ ] A fake mail backend for tests; no real mail in CI
- [ ] Setup and limits documented

### T6: Reset a forgotten password by email (Medium, 3)
**Why:** 2.5.6. Only an admin on the server can reset a password today.
**Acceptance criteria**
- [ ] A random token of at least 128 bits, stored hashed, single use, valid for at most 30 minutes
- [ ] The same answer whether or not the address exists; rate-limited
- [ ] A reset ends every session and sends a notice (T5)
- [ ] Tests for expiry, reuse, unknown address and session end

### T7: Verify the email address at sign-up (Medium, 2)
**Why:** F-a. Addresses are never proven, which is why GitHub sign-in cannot join accounts by email.
**Acceptance criteria**
- [ ] A new account must confirm its address before it can sign in or receive mail (the first admin is handled so setup is not blocked)
- [ ] Sign-up gives the same answer whether or not the address is taken
- [ ] Tests for confirm, expiry, resend and the first-admin case

### T8: Let people see and end their sessions, one or all (Medium, 2)
**Why:** 3.3.4, F-c. The list exists (`GET /api/auth/sessions`); ending one does not.
**Acceptance criteria**
- [ ] An endpoint to end one session and one to end all others, both asking for the current password
- [ ] The list marks the current session and exposes nothing that could be replayed
- [ ] A section in the account menu with device, IP and last seen
- [ ] Tests, including that ending a session logs that browser out

### T9: Check `Origin` on state-changing requests (Medium, 1)
**Why:** 4.2.2, 13.2.3. Today only `SameSite=Lax`, JSON-only bodies and the absence of CORS protect; sign-out accepts a foreign `Origin` (P10).
**Acceptance criteria**
- [ ] POST, PATCH and DELETE whose `Origin` or `Referer` host differs from the request host are refused unless listed in `CORS_ORIGINS`
- [ ] Sign-out is covered; the GitHub callback (a GET) is not affected
- [ ] Tests for same-origin, foreign, missing and allowed origins

### T10: Idle and absolute session timeouts (Medium, 2)
**Why:** 3.3.2 at level 2. Sessions last a fixed 14 days and idle time is never checked (P11).
**Acceptance criteria**
- [ ] The decision is recorded: stay at level 1 (30 days) or adopt level 2 (12 hours, or 30 minutes idle), weighing long renders
- [ ] If level 2: idle time is enforced from `last_seen_at`, and an absolute limit applies
- [ ] Tests with a fake clock; `SECURITY.md` states the limits

### T11: Protect first-run admin setup with a one-time token (Medium, 2)
**Why:** F-b. Whoever reaches a fresh deployment first becomes its admin.
**Acceptance criteria**
- [ ] A setup token is generated at first start and printed to the server log; creating the first account requires it
- [ ] An existing deployment is unaffected; the runbook in `deploy/README.md` is updated
- [ ] Tests for missing, wrong and correct token

### T12: Two-factor sign-in with an authenticator app, TOTP (Medium, 5)
**Why:** 2.3.2 and, once offered, 2.8.x. No second factor exists.
**Acceptance criteria**
- [ ] Enrol with a QR code, confirm with a code, show one-time recovery codes
- [ ] Sign-in asks for a code when enrolled; a code works once inside its window and has a defined lifetime
- [ ] Rate-limited; keys stored encrypted; disabling needs the password and a code
- [ ] Tests, including reuse of a code and a lost device

### T13: Use the `__Host-` prefix for the session cookie (Low, 1)
**Why:** 3.4.4. The cookie is called `storygen_session`.
**Acceptance criteria**
- [ ] Over HTTPS the name is `__Host-storygen_session` (`Secure`, `Path=/`, no `Domain`); plain-HTTP development keeps a plain name
- [ ] Sign-in, sign-out, the WebSocket and tests use one helper for the name
- [ ] The migration (everyone signs in again once) is noted in the release notes

### T14: Add a server-side secret to password hashing (Low, 1)
**Why:** 2.4.5, level 2 only.
**Acceptance criteria**
- [ ] The decision is recorded: implement a keyed pre-hash from an environment secret, or accept the risk
- [ ] If implemented: existing hashes keep working and are upgraded at next sign-in; rotating the secret is documented
- [ ] Tests for both hash generations

## 8. Changes since the audit

### DST-23: a limit per address, and one answer for every failed sign-in (2 November 2026)

**What changed.** Sign-in and sign-up are counted per network address in a new
table (`auth_attempts`, created automatically on an existing database), by
`auth/ratelimit.py`. Over the limit, the answer is `429` with `Retry-After`,
sent before any password is checked and saying nothing about any account. A
locked or disabled account now runs the same password check as an unknown
one and answers `401 "email or password is incorrect"`; the reason goes to the
server log (`sign-in refused: account <id> is locked`).

| Setting (`.env`, names only in `.env.example`) | Default | Meaning |
|---|---|---|
| `LOGIN_IP_MAX_FAILURES` | 20 | failed sign-ins one address may make in the window |
| `LOGIN_IP_WINDOW_MINUTES` | 15 | |
| `SIGNUP_IP_MAX_ATTEMPTS` | 10 | sign-up attempts, successful or not, per address in the window |
| `SIGNUP_IP_WINDOW_MINUTES` | 60 | |

A value that is not a positive whole number falls back to the default with a
warning; a typo never switches the limit off.

**How it works, and why.**
- The attempt is recorded and counted before the password is checked, in one
  transaction. Checking first would let forty parallel requests all see "no
  failures yet". On Postgres each address takes an advisory lock for that
  moment, because Postgres transactions cannot see each other's uncommitted
  rows. Measured on a real Postgres 16, 2 Nov 2026: a test firing 12 parallel
  wrong logins at a limit of 3 failed in 4 of 5 runs without the lock (more
  than 3 got through) and passed in 5 of 5 runs with it.
- A successful sign-in removes its own row, so people behind one shared address
  are not penalised for each other's successes. It removes nothing else, so an
  attacker cannot reset the counter by signing in to an account of their own.
  A refused attempt removes its own row too, so hammering a blocked address
  does not extend the block.
- IPv6 addresses count as their /64; `::ffff:a.b.c.d` counts as IPv4.
- Rows older than an hour are deleted by the next attempt; no cleanup job.
  The table holds IP addresses, like `sessions.ip` already does.

**Which address is counted.** `request.client`, and nothing else in the app.
Behind Caddy, uvicorn replaces it with the `X-Forwarded-For` address, but only
for a peer in `FORWARDED_ALLOW_IPS`. Production sets `*`, which is safe only
because (1) the API publishes no port, so only Caddy reaches it, and (2) Caddy
replaces the header with the real address. (2) was measured on 2 November 2026:
a throwaway `caddy:2-alpine` container (the production image) with the same
`reverse_proxy` line and no other settings, on plain HTTP on a laptop, was sent
`X-Forwarded-For: 1.2.3.4` and `1.2.3.4, 5.6.7.8`; the upstream received only
the real connecting address (`172.17.0.1`, Docker's gateway) every time. Not
tested against the live HTTPS site. The test
`test_production_trusts_forwarded_headers_only_because_the_api_has_no_public_port`
fails if the compose file loosens (1).

**Evidence.** Probes against a throwaway local instance (port 8095, fresh
`DATA_DIR`, no `.env`, no keys), Windows, Python 3.11, code `f2f90a5` plus the
DST-23 changes (uncommitted at run time). "Before" is the DST-21 audit of 30
October. Times differ between days on a laptop, so compare within a column.

| Probe | Before (30 Oct) | After (2 Nov) |
|---|---|---|
| P3 unknown email | `401` generic, median 74 ms | `401` generic, median 93 ms |
| P3 wrong password | `401` generic, 70 ms | `401` generic, 91 ms |
| P3 locked account, correct password | `401 "too many failed attempts, try again in 15 minute(s)"`, 14 ms | `401` generic, 87 ms |
| P3 disabled account, correct password | `401 "that account is disabled"`, 15 ms | `401` generic, 85 ms |
| P6 40 failed logins, 40 different emails | 40 answered `401` in 3.0 s | 20 `401` then 20 `429` in 2.1 s; first `429` at attempt 21; `Retry-After: 899` |
| P6 sign-ups from one address | 15 of 15 succeeded in 1.2 s | 16 attempts (the first account's plus 15): 10 accepted, 6 `429`, in 0.9 s |

Each After time is the median of 7 requests. After the limit, the correct
password for a real account and any password for an unknown one got the same
`429` body (compared: identical), and a forged `X-Forwarded-For` from a peer
that is not a trusted proxy did not help.

**Tests.** `tests/unit/test_login_rate_limit.py` (32 tests): under the limit
passes and a success is not counted; over it gets `429` with `Retry-After` and
costs no password check; the refusal is identical for a real and an unknown
account; `Retry-After` counts down and the limit resets after the window;
sign-up is limited whether or not it is open; one address failing does not
lock out another; the four failure cases give one status, one body and exactly
one password hash each; a forged header from an untrusted peer is ignored and
one from the trusted proxy is used; IPv6 grouping; the limit survives a
restart and is shared with a second process; 12 parallel attempts let exactly
3 through; old rows are swept; settings and defaults. Each of four behaviours
(the dummy hash, forgiving a success, trusting the header, removing a refused
attempt) was broken on purpose and a test failed each time. The whole suite
passes on SQLite (573 passed, 7 skipped) and on a real Postgres 16 (578 passed,
5 skipped), both on 2 Nov 2026, Python 3.11, code `f2f90a5` plus the DST-23
changes. The skips are environmental (no S3 server, Windows).

**Decisions recorded.**
- A locked or disabled account answers like a wrong password. A locked-out
  owner is not told why: they wait 15 minutes or an admin runs
  `python main.py users passwd <email>`. (Not done: a static line on the
  sign-in page saying that too many wrong tries lock an account.)
- Sign-up still says "that email address is already registered". Sign-ups are
  closed by default and now limited per address; email verification (T7) is the
  real fix.
- The GitHub sign-in path still says "that account is disabled", after GitHub
  has shown the visitor owns the linked identity.

**Known limits.**
- People behind one shared address share one budget of 20 failures per 15
  minutes.
- A stranger can still lock a known account (F-d): bounded to about two per
  address per window, not removed.
- Only `/login` and `/register` are limited per address. Changing a password
  needs a session, and the GitHub callback needs GitHub; neither is limited
  here.
- Each sign-in attempt adds a few small database statements.

### DST-24: password rules at the ASVS level (2 November 2026)

**What changed.** A password someone is *choosing* must now be 12 to 128
characters, may be anything printable (spaces, Unicode, emoji), has no
composition rules, is never truncated, and must not be on a bundled list of
breached passwords. For the 12-character minimum a run of spaces counts as one
character, so twelve spaces are not a password; the 128 maximum counts
characters as typed. This applies to sign-up, password change, accounts an
admin creates, and `python main.py users create` and `passwd`: all of them end
in `accounts.create` or `accounts.set_password`. Each refusal is a `400` with a
message that says what to fix and never repeats the password.

**People who already have an account keep signing in.** Sign-in, and the
"current password" field of a change, still accept up to 1,024 characters and
apply none of the new rules, so an account made under the old ones (10
characters, or 1,000) works as before and meets the new rules at its next
password change. This needed one structural change: `hash_password` used to apply
the rules itself, and signing in calls it again to upgrade an old hash, so a
10-character password would have failed to sign in whenever argon2's settings
moved on. The rules now run only where a password is chosen. A test reproduces
that upgrade and fails if the rules go back into `hash_password`.

**The list.** `auth/common_passwords.txt` holds 29,954 passwords (426,955
bytes), built by `scripts/build_common_passwords.py` from SecLists
`Passwords/Common-Credentials/Pwdb_top-1000000.txt`: commit `12274c9`, dated 22
March 2025, repository licence MIT (Copyright (c) 2018 Daniel Miessler), 8,755,171
bytes, 1,000,000 lines, SHA-256 `e9a88f67aafe6549...a18c9fc8`, downloaded on 2
November 2026. The script keeps an entry only if it could otherwise pass the
length rules (12 to 128 characters, spaces collapsed), since shorter ones are
refused anyway, folds it (NFKC, lower case) and removes duplicates. Where it
came from and its hashes are in `auth/common_passwords.meta.json`, and a test
checks the file against that record. A password is compared as typed and with
its spaces collapsed, after the same folding, so capitals and a full-width
spelling of a listed password are refused too. No password is sent anywhere.
To rebuild: download the file at that commit and run the script (its docstring
has the command). A missing or cut-short list is an error that stops sign-up,
never an empty list that lets everything through.

**Evidence.** The DST-21 probe P5 (sign-up with many passwords) repeated on a
throwaway local instance (port 8095, fresh `DATA_DIR`, no `.env`, no keys),
Windows, Python 3.11, code `99f6790` plus the DST-24 changes (uncommitted at run
time).

| Password | Before (30 Oct) | After (2 Nov) |
|---|---|---|
| 9 characters; 10 spaces | refused | refused |
| 10 and 11 characters | accepted | refused: "at least 12 characters" |
| 12, 64 and 128 characters | accepted | accepted |
| 129, 1,024 characters | accepted | refused: "at most 128 characters" |
| 1,025 characters | refused (422, no message) | refused with the same readable message |
| `1234567890`, `password12`, `qwertyuiop`, `iloveyou123` | accepted | refused (all under 12 characters) |
| `password1234`, `iloveyou1234`, `q1w2e3r4t5y6`, `PASSWORD1234` (12 or more, common) | not probed | refused: "has appeared in data breaches" |
| `a   b   c   d` (13 as typed, 7 with spaces counted once) | accepted | refused |
| 12 emoji, a passphrase with spaces, a Cyrillic passphrase | emoji accepted | all accepted |
| 96 characters, a different tail after character 72 | not truncated | not truncated: wrong tail `401`, right password `200` |

The command-line tool refused a 10-character and a listed password and
accepted a passphrase.

**Tests.** `tests/unit/test_password_rules.py` (42 tests): every rule, the two
limits exactly (12 and 128 accepted, 11 and 129 refused), spaces counted once,
the list (case, full-width, never echoed), sign-up, password change, admin
create and the command line, no truncation, accounts made under the old rules
(including the hash upgrade and the next change), the integrity of the bundled
list against its record, a missing or cut-short list, and the build script. Six
behaviours were broken on purpose and a test failed each time: the rules back
inside `hash_password`, the list ignored, the minimum back to 10, the maximum
back to 1,024, spaces counted as typed, a length rule on the request model. The
whole suite passes on SQLite (615 passed, 7 skipped) and on a real Postgres 16 (620 passed, 5 skipped),
both on 2 Nov 2026, Python 3.11, code `99f6790` plus the DST-24 changes. The
skips are environmental.

**Decisions recorded.**
- The list is local (no k-anonymity lookup), so no password leaves the server in
  any form.
- An existing account is not asked to change its password at its next sign-in;
  the ticket says the rules apply at its next change.
- Accounts an admin or the command line creates meet the same rules.
- Requirement 2.1.7 also wants the check at sign-in. It is **not** done there,
  so 2.1.7 stays open (partly fixed): an existing account whose password is on
  the list still signs in until it changes it. A notice at sign-in would close
  it; that, the strength meter (2.1.8) and the reveal toggle (2.1.12) are
  follow-up work (T4).

**Known limits.**
- The list is a floor, not a guarantee: a password that is not itself on it
  (`password1234!`, say) is accepted if it meets the length rules.
- The sign-in page still has no strength meter and no show/hide control.
- An old password of 129 to 1,024 characters works at sign-in but cannot be
  chosen again.
