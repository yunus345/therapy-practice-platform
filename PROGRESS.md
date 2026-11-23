# Progress

Single source of truth for where the project stands. Every agent keeps this file up to date (see the session protocol in `CLAUDE.md`).

## Current status

- **Phase:** 2 — Public website and psychologist panel (spec rewritten by the owner on 2026-11-23)
- **Milestone:** M0 — Stabilization, not started. Work so far is on branch `phase-1`; the last commit is Phase 1 M6, and Phase 1 M7 plus the earlier Phase 2 M1–M3 are still uncommitted.
- **Last updated:** 2026-11-23
- **Next step:** Read the new `docs/phases/phase-2.md`, list questions and present the M0 plan. The owner moves the repository out of iCloud first.
- **Waiting on owner:** moving the repository to `~/Projeler/therapy-practice-platform`; approval of the M0 plan.
- **Open questions:** none yet for the new spec. Owner-approved follow-ups: in Phase 4, a retention period of about 1 year for IP addresses and user agents, with automatic deletion, confirmed by a lawyer; automatic cancellation of `requested` appointments unanswered for 24 hours once background workers exist, with manual handling until then. A lawyer must write the KVKK disclosure text before real client data is entered.
- **Known issues:** see the 2026-11-23 audit entry below. The in-memory rate limiter counts per process.

## Phase 1 checklist

- [x] M1 — Repository and tooling
- [x] M2 — Core application skeleton
- [x] M3 — Data model and migrations
- [x] M4 — Authentication and authorization
- [x] M5 — Scheduling
- [x] M6 — Seed data
- [x] M7 — Tests (still uncommitted; committed in Phase 2 M0)

## Phase 2 checklist

Done under the earlier, agent-written spec and partly superseded: frontend foundation, account screens, client portal.

- [ ] M0 — Stabilization
- [ ] M1 — Booking without accounts
- [ ] M2 — Site settings (backend)
- [ ] M3 — Design direction (owner decision)
- [ ] M4 — Public website
- [ ] M5 — Psychologist panel
- [ ] M6 — Quality and acceptance

## Log

Newest entries first.

### 2026-11-23 — Audit and Phase 2 replanned
- Claude Code audited the uncommitted work done with GitHub Copilot (Phase 1 M7 and Phase 2 M1–M3). Backend and frontend lint and tests pass (1656 backend tests, 98% coverage), but nothing after Phase 1 M6 is committed, and iCloud created duplicate files, one of them in `frontend/src/app`.
- Audit findings: frontend error codes do not match the backend; about 97 lines of hardcoded Turkish UI text; hand-written API types instead of generated ones; the deadlock fix is untested and leaks `PROFILE_LOCK_CONFLICT`; `StrictTrue` is duplicated and the OpenAPI `const: true` was lost; the M7 log misdescribes the audit actor; unpinned CI actions and no Playwright in CI; `NEXT_PUBLIC_API_BASE_URL` in the wrong `.env.example`; Makefile and README gaps; duplicate and unused frontend packages; the earlier `phase-2.md` was written by the agent itself.
- Owner decisions: no client accounts; booking without an account, confirmed by an email code, managed through a private link; booking mode `online`, `whatsapp` or `both`, set in the panel; the WhatsApp Business API, SMS and Turnstile-like services only with approval; the psychologist manages the photo, texts, contact details, FAQ, services, prices and booking settings; design is chosen by the owner in a dedicated milestone; the repository moves out of iCloud.
- The owner rewrote `docs/phases/phase-2.md` and updated `CLAUDE.md`, added `AGENTS.md`, `.github/copilot-instructions.md` and new entries in `.claude/settings.json`.

### 2026-11-22 — Phase 2 M3 finished
- Added public psychologist profile and offered-service screens with Turkish slot selection.
- Added online appointment booking and protected own-appointments view with upcoming/past/all filters.
- Added cancellation and reschedule flows with Turkish API error-code mapping.
- Added Next.js proxy protection for `/randevularim` and `/hesabim`.
- Verified production build, 4 frontend unit tests and 4 Playwright Chromium smoke tests.

### 2026-11-22 — Phase 2 M2 finished
- Added Turkish login, registration, password reset, email verification, 2FA setup/verify and account/session screens.
- Added a CSRF-aware API client, session provider, role/MFA redirects and Turkish backend error-code mappings.
- Added Zod/react-hook-form validation and shadcn form components.
- Verified 4 frontend Vitest tests, 2 Playwright Chromium smoke tests and a production build.
- `make lint` and `make test` pass; the backend suite has 1656 passing tests at 98% coverage.

### 2026-11-22 — Phase 2 M1 finished
- Scaffolded a Next.js App Router frontend with strict TypeScript and Tailwind CSS.
- Added next-intl, TanStack Query, react-hook-form, Zod, shadcn/ui, Vitest and Playwright.
- Added a Turkish landing page, shared design tokens, API error-code handling and unit test setup.
- Added `docs/phases/phase-2.md`.
- Verified ESLint, Prettier, 2 Vitest tests, production build and 1 Playwright Chromium smoke test.
- `make lint` and `make test` pass for backend and frontend. The backend suite has 1656 passing tests at 98% coverage.

### 2026-11-21 — M7 finished
- Added strict boolean validation for KVKK confirmation fields, so JSON `1` is rejected; both registration and staff client creation are covered.
- Restored soft-deleted clients now write `clients.updated` with the acting psychologist's user ID instead of no actor.
- Profile lock deadlocks are contained by savepoints and returned as `SLOT_TAKEN` through the booking flow.
- PostgreSQL test suite: 1656 passed; total coverage 98%. `make lint` passes with no warnings.
- Migration upgrade/downgrade tests pass. `/docs` returns 200 and `/api/v1/health` returns `{"status":"ok"}`.
- `make seed` correctly refused the dev database because it already contains users; seed data was not deleted or overwritten.

### 2026-11-20 — M6 finished
- Added `make seed` (`python -m app.seed`): an admin, two psychologists, five services, 80 clients, about 840 appointments with status history and KVKK disclosures, built around the day it runs. ADR-005 documents the approach.
- The rules that decide early and late cancellations, no-shows and dropout live in `app/seed/behavior.py`, for the Phase 5 no-show model to be judged against.
- Owner decisions: the seed runs only in dev and only on a database without users; it writes no audit entries; dates follow the day it runs, with `--today` to rebuild another day.
- Added dependency: `faker` (dev group only).

### 2026-11-20 — M5 finished
- Added the catalog, client records, availability rules, time off, public profile and slots, client booking, cancellation and reschedule, staff appointments and status changes with history.
- Owner decisions: client search folds `I`, `ı`, `İ` and `i` into one letter, because the database collation does not follow Turkish case rules; admins see time off periods without their reason; client reschedules share the booking limit of 20 per session per hour.
- Decisions: bookings lock the psychologist profile shared, or exclusive when a buffer is set (ADR-003); each reschedule writes one `appointments.rescheduled` audit entry; an inactive psychologist cannot be rescheduled to (`SLOT_UNAVAILABLE`); a date range whose end is before its start returns `DATE_RANGE_INVALID`.
- Added dependency: `tzdata`. Profile timezones are checked against its list, because Linux also lists host files such as `localtime`; CI caught this.

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
