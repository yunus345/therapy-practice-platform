# Progress

Single source of truth for where the project stands. Every agent keeps this file up to date (see the session protocol in `CLAUDE.md`).

## Current status

- **Phase:** 2 — Public website and psychologist panel (spec rewritten by the owner on 2026-11-23)
- **Milestone:** M0 — Stabilization, done. CI is green on `phase-1` (hygiene, frontend, backend, e2e). The work reaches `main` through a pull request from `phase-1`, merged once the owner approves; `phase-1` is deleted afterwards.
- **Last updated:** 2026-11-23
- **Next step:** present the M1 plan (booking without accounts) and wait for approval. M1 is built on its own branch from `main`.
- **Waiting on owner:** approval to merge the `phase-1` pull request; the `main` protection rule below (set by the owner); approval of the M1 plan.
- **Workflow (owner-approved):** one branch per milestone, merged into `main` through a pull request once CI is green and the owner approves. No direct commits to `main`. CI runs on pushes to `main` and on pull requests.
- **Proposed protection for `main`** (owner sets it in GitHub): require a pull request before merging; require the status checks `hygiene`, `frontend`, `backend` and `e2e` to pass, with the branch up to date; block force pushes and deletion; apply the rules to administrators too.
- **Open questions:** none.
- **Deferred to Phase 4 (owner decision):** upgrade the GitHub Actions to their newest majors (`pnpm/action-setup` v6, `actions/setup-node` v7 and others) and pin every action to a commit SHA instead of a tag.
- **Owner-approved follow-ups:** in Phase 4, a retention period of about 1 year for IP addresses and user agents, with automatic deletion, confirmed by a lawyer; automatic cancellation of `requested` appointments unanswered for 24 hours once background workers exist, with manual handling until then. A lawyer must write the KVKK disclosure text before real client data is entered.
- **Known issues:** the in-memory rate limiter counts per process. Hardcoded Turkish strings and wrong error-code use on screens remain until M1 (out of M0 scope by the spec). The first Phase 2 frontend commit does not build on its own (noted in its message).

## Phase 1 checklist

- [x] M1 — Repository and tooling
- [x] M2 — Core application skeleton
- [x] M3 — Data model and migrations
- [x] M4 — Authentication and authorization
- [x] M5 — Scheduling
- [x] M6 — Seed data
- [x] M7 — Tests (committed in Phase 2 M0)

## Phase 2 checklist

Done under the earlier, agent-written spec and partly superseded: frontend foundation, account screens, client portal.

- [x] M0 — Stabilization
- [ ] M1 — Booking without accounts
- [ ] M2 — Site settings (backend)
- [ ] M3 — Design direction (owner decision)
- [ ] M4 — Public website
- [ ] M5 — Psychologist panel
- [ ] M6 — Quality and acceptance

## Log

Newest entries first.

### 2026-11-23 — Phase 2 M0 finished
- Pushed `phase-1`; all four CI jobs passed. CI now runs on pushes to `main` and on pull requests only, and uploads the Playwright HTML report for 7 days when e2e fails (`actions/upload-artifact`, approved).
- Owner decisions: one branch and pull request per milestone; action majors stay for now, upgrades and SHA pinning in Phase 4; `phase-1` is deleted after the merge.

### 2026-11-23 — Phase 2 M0 work committed
- Repository moved out of iCloud; environments rebuilt (`uv sync`, clean pnpm install); iCloud copies deleted. A pre-commit hook and a CI job reject names ending in ` 2`.
- Committed the earlier work as it was: Phase 1 M7, then the old Phase 2 M1, M2 and M3, then the owner's spec and agent rules.
- Profile locking: online bookings always lock `FOR UPDATE`, so no lock is upgraded and the deadlock path, its marker object and `PROFILE_LOCK_CONFLICT` are gone. ADR-003 records the trade-off (online bookings of one psychologist run one after another). A committed-transaction test covers it.
- `StrictTrue` moved to `app/core/schemas.py`; the OpenAPI schema shows `const: true` again. The M7 log entry and a code comment were corrected: the audit actor of a restore is whoever booked; both booking paths now test it.
- Error-code contract: backend `ErrorCode` enum documented on every route; generated frontend types (`openapi-typescript`, `make api-types`, CI checks both generated files); Turkish messages for every code in `messages/tr.json`, checked both ways by a Vitest test.
- Tooling: Node 26.10.0 pinned (`.nvmrc`, `engines`, engine-strict), pnpm 10.24.0 through Corepack; `make help` lists hyphenated targets; new `typecheck`; CI actions pinned; Playwright runs in CI against the real backend and a seeded database. `NEXT_PUBLIC_API_BASE_URL` moved to `frontend/.env.example`.
- Dependencies: added `openapi-typescript` (dev); removed `clsx`, `tailwind-merge`, `@testing-library/react`, `eslint-config-prettier`; kept `cn` (shadcn/ui's helper), which the owner corrected in the spec.
- Checks: `make lint` passes; `make test` passes with 1673 backend tests (98% coverage) and 7 frontend tests; 5 Playwright tests pass locally in dev and CI mode.

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
- Restored soft-deleted clients now write `clients.updated` with the booking actor instead of no actor: the client for an online booking, the psychologist for a staff booking. (Corrected in Phase 2 M0; this entry first said the acting psychologist.)
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
