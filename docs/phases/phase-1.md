# Phase 1: Foundation and backend core

Read `CLAUDE.md` and `docs/legacy-context.md` first. All rules there apply.

## Goal

A runnable, well-structured backend with the full core data model, secure authentication and role-based authorization, and a working scheduling API (availability, slot calculation, booking, status changes) that guarantees no double booking at the database level. Realistic synthetic seed data. Tests and CI from day one.

**Out of scope for this phase:** frontend (only create an empty `/frontend` placeholder with a README), payments, session notes, analytics, AI, background workers, email reminders, deployment.

## How to start

1. Read the spec end to end.
2. List any questions or ambiguities you see.
3. Present an implementation plan: steps in order, the files and modules you will create, the dependencies you intend to add (with one-line reasons), and the migrations.
4. **Wait for approval before writing code.**
5. Work in the order of the milestones below. After each milestone: run lint and tests, commit, update `PROGRESS.md`, give the Turkish summary from the session protocol, and **stop. Do not start the next milestone until the owner approves.**

## Milestone 1: Repository and tooling

- Monorepo layout as described in `CLAUDE.md`.
- Backend managed with `uv`; ruff, mypy, pytest configured in `pyproject.toml`.
- `docker-compose.yml` with:
  - PostgreSQL 17 using an image that includes pgvector (e.g. `pgvector/pgvector:pg17`), with a named volume and a healthcheck
  - a separate test database (separate service or separate database on the same server)
  - Mailpit for capturing dev emails
- `Makefile` with every target listed in `CLAUDE.md`.
- `.env.example` with every variable documented. Settings loaded with `pydantic-settings`; the app refuses to start if required secrets are missing or left at insecure defaults outside dev.
- `pre-commit` hooks for ruff and basic checks.
- GitHub Actions workflow: on push and PR, run lint, type check and tests against a Postgres service container.
- `README.md`: what the project is, architecture overview, local setup in a few commands.
- ADR-001: stack and modular monolith choice.

## Milestone 2: Core application skeleton

- FastAPI app factory, `/api/v1` router, `GET /api/v1/health` (checks DB connectivity).
- Async SQLAlchemy engine and session dependency; one transaction per request for writes.
- Global error handling producing the error shape defined in `CLAUDE.md`. Unhandled errors return a generic 500 without leaking internals.
- Structured logging (JSON in non-dev) with a request ID; a filter that prevents personal data, passwords and tokens from being logged.
- Middleware: CORS restricted to the configured frontend origin, security headers, request ID.
- Rate limiting utility usable per route. An in-memory backend is acceptable for now; document the limitation and keep the interface swappable.

## Milestone 3: Data model and migrations

Enable extensions in the first migration: `citext`, `btree_gist`, `pgcrypto`, `vector`.

Create these tables. Names and columns are a starting point; propose improvements in your plan if you see problems.

**users**: `id`, `email` (citext, unique), `password_hash`, `role` (enum: `admin`, `psychologist`, `client`), `is_active`, `email_verified_at`, `totp_secret_encrypted`, `totp_enabled`, `failed_login_attempts`, `locked_until`, `last_login_at`, `created_at`, `updated_at`

**auth_sessions**: `id`, `user_id`, `token_hash` (unique), `created_at`, `last_seen_at`, `expires_at`, `revoked_at`, `ip_address`, `user_agent`, `mfa_verified` (bool)

**auth_tokens**: one-time tokens for email verification and password reset: `id`, `user_id`, `purpose` (enum), `token_hash`, `expires_at`, `used_at`, `created_at`

**psychologist_profiles**: `user_id` (PK, FK), `first_name`, `last_name`, `title`, `slug` (unique, for the public page), `bio`, `phone`, `timezone` (IANA, default `Europe/Istanbul`), booking policy: `min_booking_notice_hours`, `max_booking_advance_days`, `cancellation_window_hours`, `auto_confirm_bookings` (bool), `created_at`, `updated_at`

**clients**: a client record is separate from a login account, because psychologists also register clients who book by phone. `id`, `user_id` (nullable, unique, FK), `first_name`, `last_name`, `email` (citext, nullable), `phone`, `birth_date` (nullable), `created_by_user_id`, `created_at`, `updated_at`, `deleted_at`

**psychologist_clients**: care relationship used for object-level authorization. `psychologist_id`, `client_id`, `created_at`, `ended_at`; unique on the pair

**services**: `id`, `name`, `description`, `duration_minutes`, `price_amount` (numeric(10,2)), `currency` (default `TRY`), `delivery_mode` (enum: `in_person`, `online`), `is_active`, `created_at`, `updated_at`

**psychologist_services**: `psychologist_id`, `service_id`, `custom_price_amount` (nullable); unique on the pair

**availability_rules**: weekly recurring working hours in the psychologist's local time. `id`, `psychologist_id`, `weekday` (0–6), `start_time`, `end_time`, `valid_from`, `valid_until` (nullable); check `start_time < end_time`

**time_off**: `id`, `psychologist_id`, `starts_at`, `ends_at`, `reason` (internal only, never exposed publicly); check `starts_at < ends_at`

**appointments**: `id`, `psychologist_id`, `client_id`, `service_id`, `starts_at`, `ends_at`, `status` (enum: `requested`, `confirmed`, `completed`, `cancelled`, `no_show`), `price_amount` and `currency` (snapshot at booking time), `booking_channel` (enum: `online`, `staff`), `booked_by_user_id`, `rescheduled_from_id` (nullable self-FK), `cancelled_at`, `cancelled_by_user_id`, `cancellation_reason` (enum: `client_request`, `psychologist_request`, `late_cancellation`, `other`), `created_at`, `updated_at`
- Check `starts_at < ends_at`.
- **Exclusion constraint**: no two appointments of the same psychologist may overlap while in status `requested` or `confirmed`:
  `EXCLUDE USING gist (psychologist_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE (status IN ('requested','confirmed'))`
- Indexes for the common queries (by psychologist and time range, by client).

**appointment_status_history**: `id`, `appointment_id`, `from_status` (nullable for creation), `to_status`, `changed_by_user_id` (nullable for system), `changed_at`, `reason`

**consents**: `id`, `client_id`, `consent_type` (enum: `kvkk_disclosure`, `data_processing`, `analytics`, `ai_processing`), `version`, `granted_at`, `revoked_at`, `ip_address`

**audit_logs**: append-only. `id`, `actor_user_id`, `action`, `entity_type`, `entity_id`, `occurred_at`, `ip_address`, `metadata` (jsonb, no personal data). The application database role must not be able to update or delete rows here; document how this is enforced.

Payments and session notes are added in Phase 3 with their own migrations.

Write ADR-002 (server-side sessions instead of JWT) and ADR-003 (double booking prevented by an exclusion constraint).

## Milestone 4: Authentication and authorization

**Endpoints** (under `/api/v1/auth`):
- `POST /register`: client self-registration (creates `users` + `clients`, role `client`). Requires acceptance of the KVKK disclosure (stored in `consents`). Sends a verification email.
- `POST /verify-email`
- `POST /login`: returns a session cookie and a CSRF token. If the user has TOTP enabled, the session starts as not MFA-verified and only `POST /2fa/verify` is allowed until it is.
- `POST /logout`: revokes the current session.
- `GET /me`
- `POST /password-reset/request` and `POST /password-reset/confirm`. The request endpoint responds identically whether or not the email exists.
- `POST /2fa/setup` (returns provisioning URI), `POST /2fa/enable`, `POST /2fa/verify`, `POST /2fa/disable` (requires password + current code).
- `GET /sessions` and `DELETE /sessions/{id}`: a user can see and revoke their own sessions.

**Rules**
- argon2id hashing; minimum password length 10; reject the most common passwords.
- Session token: high-entropy random value; only its hash is stored. Configurable idle timeout and absolute lifetime. Rotate the token on login and on privilege change.
- CSRF double-submit token required on every non-GET request.
- Lockout after repeated failed logins (configurable), plus rate limits on login, register and password reset.
- Psychologists and admins must be able to enable TOTP. Add a setting `REQUIRE_STAFF_2FA` (default `false` in dev, intended `true` in production) that blocks staff access to the panel until 2FA is enabled.
- The TOTP secret is stored encrypted with a key from the environment.
- Reusable dependencies: `current_user`, `require_roles(...)`, `require_mfa`.
- Audit log entries for login success/failure, logout, password reset, 2FA changes, session revocation.

**Admin endpoints** (role `admin` only): create a psychologist account (sends an invitation / set-password email), activate/deactivate users.

## Milestone 5: Scheduling

**Public endpoints** (no login):
- `GET /api/v1/public/psychologists/{slug}`: public profile and the services they offer.
- `GET /api/v1/public/psychologists/{slug}/slots?service_id=&from=&to=`: bookable start times. Never reveals existing appointments, clients or time-off reasons. Limit the maximum date range per request.

**Slot calculation** (in the scheduling service, fully unit-tested):
- Start from availability rules in the psychologist's timezone, subtract time off and active appointments, then fit the service duration.
- Respect `min_booking_notice_hours` and `max_booking_advance_days`.
- Slot granularity is configurable (default 15 minutes).
- Handle timezone conversion correctly; return times as ISO 8601 with offset.

**Client endpoints** (role `client`):
- `POST /api/v1/appointments`: book a slot. Re-validate the slot server-side. Status is `confirmed` if the psychologist auto-confirms, otherwise `requested`. Price snapshot taken from the service (or the psychologist's custom price). Creates the care relationship if it does not exist.
- `GET /api/v1/me/appointments`: own appointments, upcoming and past.
- `POST /api/v1/appointments/{id}/cancel`: allowed only inside the cancellation window; outside it, return an error code the UI can explain.
- `POST /api/v1/appointments/{id}/reschedule`: cancels the old appointment and creates a new one linked through `rescheduled_from_id`, in a single transaction.

**Staff endpoints** (role `psychologist`, scoped to their own data; `admin` can see all):
- Clients: list (search, pagination), create, read, update, soft delete. Only clients with a care relationship are visible.
- Services: CRUD (admin), assign to psychologists and set custom prices.
- Availability rules and time off: CRUD for the psychologist's own schedule.
- Appointments: list by date range and status, create on behalf of a client (`booking_channel = staff`), read, change status.

**Status transitions** are a single state machine in the service layer:
- `requested → confirmed | cancelled`
- `confirmed → completed | cancelled | no_show`
- `completed`, `cancelled`, `no_show` are final.
- `completed` and `no_show` are only allowed after the appointment's start time.

Every transition writes a row to `appointment_status_history` in the same transaction and an audit log entry.

**Concurrency:** booking relies on the exclusion constraint. Translate the constraint violation into a `409` with code `SLOT_TAKEN`. Do not rely on check-then-insert alone.

## Milestone 6: Seed data

A `make seed` command producing deterministic synthetic data (fixed random seed, Faker with `tr_TR` locale):
- 1 admin, 2 psychologists with profiles, weekly availability and a few time-off periods
- 4–6 services with realistic Turkish names, durations and prices
- ~80 clients, some with login accounts, most without
- ~6 months of past appointments and ~4 weeks of upcoming ones
- Realistic status distribution for past appointments, where the no-show and late-cancellation probability depends on things like booking lead time, the client's previous no-shows, weekday and hour. Keep the generating rules in one clearly documented module, because the future no-show model must be evaluated honestly against them.
- Matching status history and consent records
- Print the demo login credentials at the end (dev only; refuse to run seed when the environment is production).

## Milestone 7: Tests

Use a real PostgreSQL test database (no SQLite). Each test runs isolated (transaction rollback or a fresh schema).

Must cover at least:
- **Auth**: register, verify, login, logout, wrong password, lockout, password reset flow, session expiry and revocation, CSRF rejection, 2FA setup and enforcement.
- **Authorization matrix**: for every staff and client endpoint, verify the allowed roles succeed and others get `403`/`404`. Explicit tests that a client cannot read or change another client's appointment, and a psychologist cannot access another psychologist's clients.
- **Slot calculation**: working hours, time off, existing appointments, notice and advance limits, durations that do not fit, timezone boundaries.
- **Double booking**: two concurrent booking requests for the same slot; exactly one succeeds, the other gets `SLOT_TAKEN`.
- **State machine**: every allowed and disallowed transition; history rows written.
- **Cancellation window** and **reschedule** atomicity.
- **Logging**: a test that personal data and passwords do not appear in logs for the auth flows.

Report the coverage of the `service.py` files.

## Acceptance criteria

- `make up && make migrate && make seed && make dev` gives a running API with interactive docs at `/docs`.
- `make lint` and `make test` pass locally and in CI.
- All migrations apply from scratch and downgrade cleanly.
- No endpoint returns data the caller is not authorized to see.
- README explains setup and the architecture; ADR-001, 002, 003 exist.
- Final summary: what was built, decisions made, deviations from this spec with reasons, known limitations, and suggested topics for Phase 2.
