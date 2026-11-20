# Progress

Single source of truth for where the project stands. The agent keeps this file up to date (see the session protocol in `CLAUDE.md`).

## Current status

- **Phase:** 1 — Foundation and backend core
- **Milestone:** M5 done, on branch `phase-1`
- **Last updated:** 2026-11-20
- **Next step:** M6 — Seed data, after owner approval.
- **Waiting on owner:** approval to start M6.
- **Open questions:** none. Owner-approved follow-ups: in Phase 4, a retention period of about 1 year for IP addresses and user agents, with automatic deletion, confirmed by a lawyer; automatic cancellation of `requested` appointments unanswered for 24 hours once background workers exist, with manual handling until then. A lawyer must write the KVKK disclosure text before real client data is entered. The repository stays on the iCloud-synced Desktop for now; move it if sync causes trouble.
- **Known issues:** `make seed` exits with an error until M6. The in-memory rate limiter counts per process. Restoring a soft-deleted client writes a `clients.updated` audit entry without an actor. Boolean confirmation fields typed `Literal[True]` also accept the JSON number `1`. A rare deadlock while a booking upgrades to an exclusive profile lock returns 500 instead of `SLOT_TAKEN`. Slot calculation is untested for zones with unusual DST rules such as `America/Nuuk`. To be fixed in M7.

## Phase 1 checklist

- [x] M1 — Repository and tooling
- [x] M2 — Core application skeleton
- [x] M3 — Data model and migrations
- [x] M4 — Authentication and authorization
- [x] M5 — Scheduling
- [ ] M6 — Seed data
- [ ] M7 — Tests

## Log

Newest entries first.

### 2026-11-20 — M5 finished
- Added the catalog, client records, availability rules, time off, public profile and slots, client booking, cancellation and reschedule, staff appointments and status changes with history.
- Owner decisions: client search folds `I`, `ı`, `İ` and `i` into one letter, because the database collation does not follow Turkish case rules; admins see time off periods without their reason; client reschedules share the booking limit of 20 per session per hour.
- Decisions: bookings lock the psychologist profile shared, or exclusive when a buffer is set (ADR-003); each reschedule writes one `appointments.rescheduled` audit entry; an inactive psychologist cannot be rescheduled to (`SLOT_UNAVAILABLE`); a date range whose end is before its start returns `DATE_RANGE_INVALID`.
- Added dependency: `tzdata`.

### 2026-11-02 — M4 finished
- Added registration, email verification, login, logout, sessions, password reset, TOTP 2FA with recovery codes, admin account actions, audit entries, CSRF protection, email delivery and `make create-admin`.
- Added beyond the spec: `GET /auth/csrf`, `POST /auth/verify-email/resend` (a client could otherwise never verify after the link expires, and M5 blocks unverified booking), `POST /admin/users/{id}/2fa/reset`.
- Decisions: failed logins persist through `AppError.keep_transaction` instead of a separate transaction, which keeps tests on one rolled-back connection; an invitation uses `POST /auth/password-reset/confirm`; admins cannot deactivate or reset themselves; the 2FA rate limit counts per session, because colleagues share an office IP; the startup check fails if it finds no routes, so an upgrade of FastAPI cannot silently disable it.
- Added dependencies: `argon2-cffi`, `pyotp`, `cryptography`, `email-validator`, `aiosmtplib`.

### 2026-11-02 — M3 finished
- Added all Phase 1 models, six reversible migrations, ADR-002, ADR-003 and ADR-004.
- Added beyond the spec, as approved: `auth_recovery_codes`, `users.totp_last_used_step`, `consents.recorded_by_user_id`, `psychologist_profiles.buffer_minutes`, the `rescheduled` cancellation reason and `invitation` token purpose. `appointment_status_history.reason` became the enum column `reason_category`.
- Decisions: migrations are the source of truth for CHECK and EXCLUDE constraints; `appointment_status_history` is append-only for the application role; each appointment can be rescheduled at most once; `updated_at` is set by a trigger, except on the two append-only tables.
- Owner confirmed the recommendations on the open questions. `.env` was verified absent from git history and from GitHub.

### 2026-11-02 — M2 finished
- Added the app factory, `GET /api/v1/health`, async sessions, the error shape, structured logging with redaction, middleware (request ID, security headers, CORS, generic 500) and the swappable rate limiter. Added `structlog`.
- Fixed CI: the first run failed because `astral-sh/setup-uv` has no `v10` tag. Actions now use full version tags, and the runner is pinned to `ubuntu-24.04`.
- Decisions: write sessions use `Depends(scope="function")`, so the commit finishes before the response; request IDs are always generated, never accepted from clients; exception messages never reach logs; `/health` is limited to 120 requests per minute per IP.
- Note: the owner confirmed the phase-1 push. The GitHub repository is public.

### 2026-11-02 — M1 finished
- Owner approved the Phase 1 plan, including all 12 recommendations from the planning questions and the listed decisions.
- Added the `backend` project (uv, Python 3.14), settings validation, Docker Compose, Makefile, pre-commit, CI, README and ADR-001. Removed the tracked `.DS_Store`.
- Decisions: `ENVIRONMENT` defaults to production so that checks fail closed; two database roles from day one; Alembic is configured with no migrations yet, so `make migrate` works ahead of M3; `/docs` is disabled in production.
- Spec additions: a minimal `app/main.py` and the Alembic setup arrive in M1 to keep the Makefile targets working.
- Open questions: see "Current status".

### 2026-11-01 — Project initialized
- Added `CLAUDE.md`, `docs/phases/phase-1.md`, `docs/legacy-context.md`, `PROGRESS.md` and `.claude/settings.json`.
- Decisions: Next.js + FastAPI + PostgreSQL, modular monolith, English code and docs, Turkish UI, phased delivery with owner approval between milestones.
- No application code yet.
