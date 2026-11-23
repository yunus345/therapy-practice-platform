# CLAUDE.md

Guidance for AI coding agents working in this repository. Read this file fully before every task.

These rules apply to **every** coding agent working here (Claude Code, GitHub Copilot, Codex or any other). `AGENTS.md` and `.github/copilot-instructions.md` point other agents to this file.

## Project

**Seans** (working title) is a practice website and management platform for a psychologist.

- Two sides:
  - a **public website**: introduces the psychologist (about, services, prices, FAQ, contact) and lets visitors book an appointment **without creating an account**, or reach the psychologist on WhatsApp, depending on the booking mode the psychologist chooses;
  - a **psychologist panel**: calendar, appointments, clients, site content and settings, later payments, reports and optional session notes.
- **Clients have no accounts and no logged-in area.** Only staff (`admin`, `psychologist`) log in. A client manages a booking through a private link sent by email.
- The psychologist must be able to manage the site content (photo, texts, contact details, services and prices, booking settings) from the panel, without touching code.
- It is a portfolio project, but it **may be used by a real psychologist with real client data**. Treat every security and privacy decision as production-grade. Health data is special-category personal data under Turkish KVKK.
- A data science / AI layer (analytics, no-show prediction, assistant agent, RAG) will be added in later phases. Design data capture with that in mind, but do not build it before its phase.

## Communication

- The project owner writes in **Turkish**. Always reply to them in Turkish: plans, questions, status updates and summaries.
- Everything written into the repository stays in **English**: code, identifiers, comments, docstrings, commit messages, docs, ADRs. The only exception is user-facing UI text, which is Turkish and lives in the i18n message files.
- Do not switch repository content to Turkish because the conversation is in Turkish.

## How we work

- **Phase by phase.** The current phase spec lives in `docs/phases/`. Implement only what the active phase asks for. Do not start the next phase.
- **Plan first.** For each phase or large task, present an implementation plan and wait for approval before writing code.
- **Ask, don't assume.** If the spec is ambiguous or a decision has trade-offs, stop and ask. Do not silently pick.
- **No new dependencies without asking.** Propose the package and the reason first.
- **Record decisions.** Any significant architectural decision gets a short ADR in `docs/adr/NNN-title.md` (context, decision, alternatives, consequences).
- **Small, focused commits** using Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).
- **Keep docs in sync.** Update `README.md`, `.env.example` and the relevant docs whenever behavior or setup changes.
- There is an older prototype of this idea in another repository. **Do not read, copy or reference it.** Its domain concepts are summarized in `docs/legacy-context.md` if that file exists; that summary is the only input from it.

## Boundaries

The owner works on this project occasionally and must always be able to follow what happened. Move in small, reviewable steps.

**Stop and wait for explicit approval:**
- before writing code for a new phase or milestone (present the plan first),
- after finishing each milestone (report, then wait; never roll into the next one on your own),
- before anything outside the scope of the current task, even if it looks like an obvious improvement. Note it under "Open questions" in `PROGRESS.md` instead.

**Never do without explicit approval in the conversation:**
- push to a remote, rewrite git history, force-push, or delete branches,
- delete or overwrite files unrelated to the current task, or delete many files at once,
- drop databases, remove Docker volumes, or run destructive migrations,
- add, remove or upgrade dependencies,
- create or edit `CLAUDE.md`, `AGENTS.md`, files in `docs/phases/`, or `.claude/settings.json` (propose the change instead),
- disable, skip or weaken tests, lint rules or type checks to make something pass.

**Phase specs come from the owner.** Never write a phase spec yourself. If the next phase has no spec in `docs/phases/`, stop and tell the owner.

**Design and user experience are the owner's decisions.** Before any work that sets or changes how the product looks or feels (colors, typography, layout, page structure, navigation, wording of key screens, new screens), present two or three concrete options, with previews where possible, and wait for the owner to choose. Record the decision in `docs/design.md`. Implementing an already approved design needs no new approval.

`.claude/settings.json` enforces some of these at the tool level for Claude Code only. Other agents have no such guard, so follow these rules yourself. Do not try to work around them.

## Session protocol

`PROGRESS.md` at the repository root is the single source of truth for where the project stands.

**At the start of every session**, read `PROGRESS.md` and give the owner a short status briefing in Turkish before doing anything else: current phase and milestone, what was done last, what is next, and anything waiting on the owner. If the owner writes "durum", give the same briefing.

**At the end of every response in which files changed**, finish with this summary in Turkish:

```
### Özet
- Yapılanlar: ...
- Değişen dosyalar: ...
- Alınan kararlar: ...
- Nasıl kontrol edilir: (commands to run or things to look at)
- Açık sorular: ...
- Sıradaki adım: ...
```

**Before ending a work session or after finishing a milestone**, update `PROGRESS.md`:
- rewrite the "Current status" section so it is accurate,
- tick completed items in the milestone checklist,
- add a dated entry at the **top** of the log (date, what was done, decisions, open questions). Newest entry first. Keep entries short; details belong in commits and ADRs.

**Commit as you go.** Never leave finished work uncommitted. Commit locally at least once per milestone, in small focused commits, before reporting the milestone as done. Pushing still needs the owner's approval.

**Report honestly.** `PROGRESS.md` and summaries must describe what the code actually does. If a fix is not covered by a test, say so.

## Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js (App Router) + TypeScript (strict), Tailwind CSS, shadcn/ui |
| Frontend data | TanStack Query, react-hook-form + Zod, API types generated from the backend OpenAPI schema |
| Frontend i18n | next-intl. UI language is **Turkish**; no hardcoded user-facing strings in components |
| Backend | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x (async), Alembic |
| Database | PostgreSQL 17 with extensions `citext`, `btree_gist`, `pgcrypto`, `vector` (pgvector) |
| Tooling | uv (Python deps), ruff (lint + format), mypy, pytest; pnpm, ESLint, Prettier, Vitest, Playwright |
| Local infra | Docker Compose (Postgres, Mailpit for dev email) |

Use the latest stable versions unless a phase spec says otherwise.

## Architecture

Separated frontend + **modular monolith** backend. No microservices.

```
/backend
  app/
    core/            config, database, security, errors, logging, middleware
    modules/
      <module>/
        router.py      HTTP layer only: parse, call service, return schema
        schemas.py     Pydantic request/response models
        service.py     business logic + authorization
        repository.py  database access only
        models.py      SQLAlchemy models
    main.py
  alembic/
  tests/
/frontend
/docs
  adr/
  phases/
docker-compose.yml
Makefile
```

Planned modules: `auth`, `users`, `clients`, `scheduling`, `catalog` (therapy services offered and their prices; named `catalog` to avoid confusion with the service layer), `consent`, `audit`, `notifications`, `billing`, `clinical`, later `analytics` and `ai`.

**Layer rules**
- Routers stay thin. No business logic, no direct DB access.
- Services own business logic **and** authorization checks. The future AI agent will call services as tools, so a service must be safe to call without the router in front of it.
- Repositories only talk to the database.
- A module never imports another module's repository. Cross-module calls go through the other module's service.

## Security and privacy rules (non-negotiable)

**Data classification**
- *Public*: profile, photo, services and prices, contact details the psychologist chooses to publish, free slots (never who booked them).
- *Operational*: names, contact details, appointment times, payments.
- *Clinical*: session notes and any health/condition information. Stored with field-level encryption; never collected through the public booking flow; only the authoring psychologist can read it; every read is audit-logged; the whole module is behind the `CLINICAL_NOTES_ENABLED` flag.

**Authentication**
- Passwords hashed with argon2id. Never stored or logged in plain text.
- Server-side sessions: opaque random token in an `httpOnly`, `Secure`, `SameSite=Lax` cookie; only a hash of the token is stored in the DB. Sessions are revocable and expire.
- CSRF protection on all state-changing requests (double-submit token).
- TOTP two-factor authentication for staff roles (`admin`, `psychologist`).
- Rate limiting and temporary lockout on login and password reset.
- Only staff have accounts. Staff accounts cannot self-register; only an admin creates them.

**Public booking (no client accounts)**
- A booking is confirmed only after a one-time code sent to the client's email. Codes are hashed, short-lived and limited in attempts.
- A client manages a booking through a private link: a high-entropy random token, stored hashed, valid for exactly one appointment, expiring after it. Pages opened with it are `noindex` and send no referrer.
- Public endpoints never reveal whether an email or phone already belongs to a client. Responses are identical either way.
- Public forms are rate limited per IP and per email.

**Authorization**
- Deny by default. Every endpoint declares which roles may call it.
- Object-level checks on every read and write (prevent IDOR): a booking token reaches only its own appointment; a psychologist sees only clients they have a care relationship with.
- Tests must cover the authorization matrix, including cross-user and cross-token access attempts.

**File uploads**
- Accept only the expected image types, checked by decoding the file, not by its name or declared type. Enforce a size limit.
- Re-encode images on the server, strip metadata (EXIF, location), store them under random names and never serve uploaded SVG or HTML.

**Third parties on public pages**
- No trackers, analytics, embedded maps or remotely loaded fonts without the owner's approval. They send visitors' data to third parties, which matters under KVKK. Self-host fonts.

**Data handling**
- Never log passwords, tokens, personal data or clinical content. Log IDs, not names.
- Validate all input with Pydantic on the backend and Zod on the frontend.
- Timestamps are `timestamptz`, stored in UTC; display in the user's timezone (default `Europe/Istanbul`).
- Money is `numeric(10,2)` plus a currency code. Never use floats for money.
- Client records are soft-deleted (`deleted_at`); define retention and hard-delete paths explicitly when needed.
- Secrets only via environment variables. Keep `.env.example` complete; never commit real secrets.
- Collect the minimum data needed. The booking form must not ask for health information.

## Conventions

- Code, identifiers, table and column names, commit messages and docs: **English**. User-facing UI text: **Turkish**, via message files.
- Tables: plural `snake_case` (`appointments`). Primary keys: UUID (`gen_random_uuid()`).
- API: versioned under `/api/v1`, resource-oriented, JSON.
- Errors: one consistent shape: `{"error": {"code": "SLOT_TAKEN", "message": "...", "details": {...}}}`. The frontend maps `code` to Turkish messages; never show raw backend messages to users.
- Every model has `created_at` and `updated_at`.
- Prefer database constraints (unique, check, exclusion, foreign keys) over application-only checks for data integrity.
- Enums for fixed value sets (statuses, roles, methods). No free-text status columns.

## Data capture for the future data science layer

- Never overwrite history that a model could learn from. Appointment status changes go to `appointment_status_history` with timestamp, actor and reason.
- Keep booking metadata: `created_at`, booking channel, who booked, rescheduled-from link, cancellation time and reason category.
- Analytics and AI processing of personal data require recorded consent (`consents` table). Clinical content is excluded from AI features unless explicitly enabled and consented.

## Commands

The Makefile is the entry point. Keep these targets working:

```
make up          # start local infra (Postgres, Mailpit)
make down        # stop local infra
make dev         # run backend in dev mode
make migrate     # apply Alembic migrations
make seed        # load synthetic demo data
make test        # run backend and frontend tests
make lint        # backend and frontend lint and type checks
make fmt         # format code
```

Frontend-specific targets may be added; keep `make help` listing all of them and keep the README's prerequisites (including the Node version used in CI) accurate.

## Definition of done

A task is done only when:
- the code follows the layer and security rules above,
- tests for new behavior pass, including authorization and edge cases,
- `make lint` and `make test` pass with no new warnings,
- migrations are included and reversible,
- docs, `.env.example` and ADRs are updated where relevant,
- the work is committed in small commits,
- `PROGRESS.md` is updated and the Turkish summary from the session protocol is given.

## Roadmap

1. **Foundation and backend core**: repo setup, schema, auth, roles, scheduling API, audit, seed data
2. **Public website and psychologist panel**: stabilization, booking without accounts, site settings, owner-approved design, public pages with SEO, panel screens
3. **Billing, clinical notes, reporting**: payments, encrypted session notes, reports
4. **Hardening and delivery**: end-to-end tests, CI/CD, deployment, backups, monitoring
5. **Data science and AI**: analytics dashboard, no-show prediction, assistant agent, RAG
