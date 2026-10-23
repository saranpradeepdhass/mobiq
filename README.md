# Mobiq — Mobile Bill Splitter

Streamlit app backed by Google Sheets for splitting monthly mobile bills: per-line shares, partial payments, historical dues, email sign-in, guest policy, and an admin console. Python 3.11+, `decimal.Decimal` for all money.

## Status: what is and isn't verified

| Verified here | **Not** verified here |
|---|---|
| 93 pytest tests pass (business rules, services, auth, authorization, UI tokens, Sheets adapter against a **fake** gspread client) | Any call to the **real** Google Sheets API (no credentials were available) |
| The app boots headless and walks guest / user / admin flows via Streamlit `AppTest` in demo mode | Real SMTP delivery of sign-in codes |
| | Any hosted deployment (Streamlit Community Cloud etc.) |
| | Visual rendering in a real browser (CSS is covered by token/structure tests only) |

Follow the *smoke test* below with your own credentials before relying on it.

## Assumptions

1. **Invitation model for registration.** An admin invites an email (creates an active row in `Users`); the invitee then signs in with an emailed code. Unknown or inactive emails never get a code and see the same message as valid ones. The very first admin is created from `auth.bootstrap_admin_email`, only while no admin exists.
2. **Admins mark shares paid.** Users cannot submit payments. On *Shares & payments* each share has a **Paid** switch: on = one confirmed payment for the remaining balance; off = a reversal of every confirmed payment. There is no UI for partial payments, pending/rejected states or manual reversals; the calculations still honour such rows if they exist in the sheet (confirmed only).
2a. **Notify (manual reminders).** Each unpaid share on *Shares & payments* has a **Notify** button that emails the line owner the due details (line, month, amount due, paid so far, balance, due date, status, other unpaid bills, payment instructions). It only sends when an admin clicks it, needs `[smtp]` in secrets, is refused for paid/draft shares and inactive owners, is limited to one email per share per 15 minutes, and is recorded in `AuditLog` (no message body stored). In demo mode emails are recorded in memory, not sent. Nothing is ever sent automatically.
3. **Status precedence:** `Paid` > `Overdue` > `Partial` > `Due`. A partially paid share past its due date shows *Overdue* (paid amount still shown). Due today is not overdue.
4. **"Total collected"** = confirmed payments (capped at the amount due) against shares in the *current* period. Earlier months appear only under *Previous arrears* / *Historical dues*.
5. **Draft bills are invisible to users**; closed bills stay in historical dues until paid.
6. Overpayment is rejected (confirmed payments may not exceed the amount due).
7. Marking a share unpaid never edits history: it appends a **reversal** (negative confirmed row with `reverses_payment_id`). This adds one column to `Payments`; `Bills.line_ids` (comma-separated line ids; replaces a separate `BillLines` sheet) and the `AuditLog` worksheet are also additions to the spec'd model.
8. `BillShares.amount_paid` / `payment_status` are written back for sheet readability but **all calculations derive from `Payments`**; never edit them by hand.
9. Rows edited directly in the sheet are validated on load; malformed rows are skipped and listed as warnings in the admin dashboard.

## Architecture

```
app.py                      entry: config, session, login, navigation, header
pages/dashboard.py          read-only dashboard (UI only)
pages/admin_console.py      admin UI; every action calls a service
services/
  repository.py             Repository interface + InMemoryRepository (tests/demo)
  google_sheets_service.py  Google Sheets implementation (cache, retry, optimistic concurrency)
  auth_service.py           email-code auth, rate limiting, principal resolution
  access_service.py         read-side authorization (user/admin/guest views)
  bill_service.py           bills, bill lines, shares (publish/close/confirmations)
  payment_service.py        paid/unpaid toggle (append-only payments + reversals)
  directory_service.py      users, mobile lines, settings
  reminder_service.py       extension points (reminders, PDF import) - inert by default
  common.py                 Principal, require_admin, audit log, errors
  runtime.py, demo_data.py  wiring from secrets; SAMPLE data
core/                       models, worksheet schemas, validated snapshot loader
utils/                      calculations (pure), validators, formatting
ui/                         theme.py (CSS tokens), components.py (HTML builders), charts.py
tests/  scripts/init_sheet.py  sheets_template/ (CSV headers + SAMPLE rows)
```

Data access is behind `Repository` (`list / append / update / get / lock / invalidate`). To migrate to a database, implement that interface and return it from `services/runtime.py`; services and UI do not change.

### Concurrency and caching
Reads are cached per worksheet (`cache_ttl_seconds`, default 30) and invalidated on every write. Writes re-read fresh, check `updated_at` (optimistic concurrency → "someone else changed this record"), and are serialised per process by a lock. Google Sheets has no transactions: two *separate server processes* writing the same row within milliseconds can still race. Fine for a small household; use a database beyond that.

## Security model

- **Sign-in mode (`auth.require_code`, default `false`):** the default is **email-only sign-in** - the email must belong to an active user in `Users`, but nothing proves the person owns that mailbox. Anyone who knows or guesses an active user's email, **including an admin's**, can sign in as them and see or change all financial data. Attempts are rate limited, which only slows guessing. Set `require_code = true` (and configure `[smtp]`) for emailed one-time codes.
- **Identity (code mode):** 6-digit code by email, HMAC-hashed at rest, single-use, expires (default 10 min), max 5 attempts, per-email cooldown (30 s) and cap (3 per 15 min), global cap. Codes are never logged or stored in plaintext.
- **Roles come from the `Users` sheet**, re-read on every run (`resolve_principal`) and on every sensitive operation (`require_admin`). The session stores only a user id. Demoting or deactivating someone takes effect immediately; a forged `role="admin"` principal is rejected (tested).
- **Authorization** is enforced in services, not just by hiding the Admin Console nav item.
- **Guests** see nothing by default. `guest_visibility` setting: `none` | `aggregate` (combined totals, no names) | `status_only` (anonymised "Line 1…" statuses). Treat guest mode as public.
- **Privacy:** phone numbers are shown as `•••• 1234`; audit entries never contain full numbers, codes or credentials. All dynamic HTML is escaped. Sheet writes use `RAW` so values are never evaluated as formulas.
- **Secrets** live only in Streamlit secrets / env. `.streamlit/secrets.toml` is git-ignored.
- **Audit trail:** `AuditLog` is append-only; payments are never edited or deleted.

### Hosting and session limitations
- Streamlit sessions live in server memory: **refreshing the browser tab signs the user out**, and a server restart invalidates everything (including pending codes and rate-limit counters, which are per-process).
- Run a single process/replica; multiple replicas do not share OTP or rate-limit state.
- Streamlit Community Cloud may sleep idle apps and restarts them periodically.
- `dev_show_code = true` prints the sign-in code on screen: local development only.

## Setup

### 1. Google Cloud and the spreadsheet
1. Create a project at <https://console.cloud.google.com>, enable the **Google Sheets API** (and Google Drive API if `gspread` asks for it).
2. *IAM & Admin → Service Accounts → Create*. No project roles are needed. *Keys → Add key → JSON* and download it.
3. Create a Google Sheet. Share it with the service account's `client_email` as **Editor** (the minimum that allows writes). Do not share it publicly.
4. Copy the sheet id from its URL (`/d/<ID>/edit`).
5. Create the worksheets: either import the CSVs in `sheets_template/` (one CSV per tab, tab names must match exactly), or run `python scripts/init_sheet.py --init [--sample]` after step 7.
   `sheets_template/*.csv` contain headers plus **SAMPLE** rows (`SAMPLE` in names/notes, `example.com` emails). Replace the sample users with real ones, or delete the sample rows, before real use.

### 2. Local run
```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```
Credential-free demo (in-memory SAMPLE data; sign-in codes shown on screen; data resets on restart):
```powershell
$env:MOBIQ_BACKEND="demo"; .\.venv\Scripts\python -m streamlit run app.py
```
Sign in as `admin@example.com` or `alex@example.com` (Alex has two lines and arrears).

### 3. Real configuration
Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in: `[gsheets]` (spreadsheet id + the service-account JSON fields), `[auth]` (`secret`, `bootstrap_admin_email`), `[smtp]` (any SMTP provider). Then:
```powershell
.\.venv\Scripts\python -m streamlit run app.py
```
Sign in with `bootstrap_admin_email`, open **Admin Console**, invite users, add lines, create a bill, split, publish.

### Smoke test (do this once with real credentials)
1. App starts without a configuration error. 2. Sign-in email arrives and the code works. 3. Create a draft bill → rows appear in `Bills`, `BillShares`, `AuditLog`. 4. Publish, record a confirmed payment → `Payments` row, `BillShares.amount_paid` updates. 5. Edit the same bill in the sheet and in the app to see the conflict message.

### Deployment (Streamlit Community Cloud, untested here)
Push the repo (without `secrets.toml`), create the app pointing at `app.py`, paste the secrets TOML into *Settings → Secrets*. Pin Python 3.11+ in the app settings. Read the limitations above first.

## Tests
```powershell
.\.venv\Scripts\python -m pytest -q
```
Coverage: paid/due/partial/overdue; current payment with previous arrears; multiple lines per email with independent payment histories; shared bills without duplicate totals; multiple partial payments; duplicate prevention; invalid amounts; history preserved on number change/deactivation; reversals; guest restrictions; admin authorization (non-admin, guest, forged, demoted); auth failures (wrong/expired/replayed codes, inactive users, rate limits); Sheets adapter caching/retry/failure handling (fake client); malformed rows; theme tokens (single accent, responsive CSS, focus styles); HTML escaping; headless app flows.

## Design system
Tokens live in `ui/theme.py` (canvas `#0A0A0F`, surface `#161B22`, accent `#7C3AED` only, text `#FFFFFF`/`#8B949E`/`#484F58`, hairline borders) and are mirrored in `.streamlit/config.toml`. Payment states use a green (Paid) / yellow (Partial) / red (Due, Overdue) cue, always with a text label and glyph. The tokens test fails if any other hex colour is introduced.

## Extension points (inert until configured)
- **Reminders** — `services/reminder_service.py`: `plan_reminders()` only proposes; `dispatch()` raises unless the `reminders_enabled` setting is on, an admin calls it, and a `ReminderChannel` (email/push/SMS) is explicitly supplied.
- **AT&T PDF import** — implement `BillExtractor`; its output must be turned into a *draft* bill and reviewed; publishing always needs explicit confirmation.
- **Payment instructions** — `payment_instructions` setting, shown on the dashboard.
- **Database** — implement `Repository`.
