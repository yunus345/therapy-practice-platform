# Progress

Single source of truth for where the project stands. The agent keeps this file up to date (see the session protocol in `CLAUDE.md`).

## Current status

- **Phase:** 1 — Foundation and backend core
- **Milestone:** M1 done, on local branch `phase-1`
- **Last updated:** 2026-11-02
- **Next step:** M2 — Core application skeleton, after owner approval.
- **Waiting on owner:** approval to start M2; approval to push `phase-1` so that CI can run on GitHub.
- **Open questions:** retention period for IP addresses and user agents; expiry of unconfirmed `requested` appointments, which hold their slot indefinitely until background workers exist; session buffer time between appointments; real KVKK disclosure text needs legal review.
- **Known issues:** the GitHub Actions workflow has not run on GitHub yet. Its bootstrap script and test suite were verified locally against a throwaway `pgvector/pgvector:pg17` container. `make seed` exits with an error until M6.

## Phase 1 checklist

- [x] M1 — Repository and tooling
- [ ] M2 — Core application skeleton
- [ ] M3 — Data model and migrations
- [ ] M4 — Authentication and authorization
- [ ] M5 — Scheduling
- [ ] M6 — Seed data
- [ ] M7 — Tests

## Log

Newest entries first.

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
