# Seans

Seans (working title) is a practice management and online booking platform for psychologists. It has two sides: a client portal with public profiles, online booking and personal appointments, and a psychologist panel with calendar, clients, appointments and reports.

The project is in Phase 1, which builds the backend core. See [`PROGRESS.md`](PROGRESS.md) for the current status and [`docs/phases/`](docs/phases/) for the phase specs.

## Architecture

The system is a separated frontend plus a modular monolith backend.

- **Backend:** Python, FastAPI, SQLAlchemy (async), Alembic.
- **Database:** PostgreSQL 17 with `citext`, `btree_gist`, `pgcrypto` and `vector`.
- **Frontend:** Next.js, arriving in Phase 2.
- **Local infrastructure:** Docker Compose runs Postgres and Mailpit.

Each backend module in `backend/app/modules/` has its own router, schemas, service, repository and models. Routers stay thin, services own business logic and authorization, and repositories only access the database. Modules call each other only through services. See [ADR-001](docs/adr/001-stack-and-modular-monolith.md).

### Database roles

The application connects with a restricted role (`seans_app`). Migrations run as the schema owner (`seans_owner`). The restricted role is created by [`docker/postgres/init/01-roles-and-databases.sh`](docker/postgres/init/01-roles-and-databases.sh), which also creates the test database. Docker runs this script only when the data volume is empty.

The restricted role cannot change `audit_logs` or `appointment_status_history`, and triggers block changes to `audit_logs` for every role. See [ADR-004](docs/adr/004-audit-log-immutability.md).

### Data model

Models live in each module's `models.py`. `CHECK` and `EXCLUDE` constraints and triggers live in `backend/alembic/versions/`, which are the source of truth for them. A test fails if the models and the migrations drift apart. Double booking is prevented by a database constraint, see [ADR-003](docs/adr/003-exclusion-constraint-for-double-booking.md).

### Cross-cutting behavior

- **Errors:** every error uses `{"error": {"code", "message", "details"}}`. Unhandled errors return a generic 500. Validation errors list field locations and never echo submitted values.
- **Transactions:** routes that write use `WriteSession`, which opens one transaction per request and commits before the response is sent.
- **Logging:** logs are JSON outside dev and carry a request ID, which is also returned as `X-Request-ID`. `app/core/logging.py` redacts sensitive keys, emails, phone numbers and bearer tokens. Exception messages are dropped and only types and code locations are kept, because messages can quote personal data. Access logs contain the route template, never the raw path or query string.
- **Rate limiting:** use `Depends(RateLimit("scope", limit, window_seconds))` on a route. The default backend is in-memory, so with several workers each one counts separately and a restart clears the counters. Implement `RateLimitBackend` to replace it with a shared store before scaling out.

### Authentication and access

- **Sessions:** login sets an opaque token in an `httpOnly` cookie, and only its hash is stored. See [ADR-002](docs/adr/002-server-side-sessions.md).
- **CSRF:** every non-GET request under `/api/v1` needs a signed double-submit token: the `seans_csrf` cookie plus the same value in the `X-CSRF-Token` header. Get one from `GET /api/v1/auth/csrf` before login. Login and 2FA return a new one.
- **Access policies:** every API route must declare `Public`, `current_user`, `require_mfa`, `require_roles(...)` or `require_session_roles(...)`. The application refuses to start if a route declares none. Services also check `Actor`, so they stay safe to call without a router.
- **2FA:** TOTP is available to staff only. A session stays limited until it is verified, and with `REQUIRE_STAFF_2FA=true` staff must enable 2FA first. Enabling it returns 8 single-use recovery codes. An admin can reset 2FA for a staff member who lost access.
- **Rejected requests:** an `AppError` with `keep_transaction = True` still commits, which keeps failed login counters and audit entries.
- **Common passwords:** `app/core/data/common_passwords.txt` holds the entries of at least 10 characters from the NCSC top 100,000 list in [SecLists](https://github.com/danielmiessler/SecLists) (MIT).

## Local setup

You need Docker, [uv](https://docs.astral.sh/uv/) and make.

```sh
make up                           # creates .env from .env.example, starts Postgres and Mailpit
cd backend && uv sync && cd ..    # installs the Python toolchain and dependencies
make migrate
make dev                          # API on http://localhost:8000, docs on /docs
```

Mailpit shows captured dev email on <http://localhost:8025>.

To enable the pre-commit hooks once:

```sh
cd backend && uv run pre-commit install
```

## Commands

| Command | Purpose |
|---|---|
| `make up` | Start local infrastructure |
| `make down` | Stop local infrastructure, keeping data |
| `make dev` | Run the backend in dev mode |
| `make migrate` | Apply Alembic migrations |
| `make seed` | Load synthetic demo data (available from Milestone 6) |
| `make create-admin EMAIL=you@example.com` | Create an admin account, asking for the password |
| `make test` | Run backend tests against the test database |
| `make lint` | Run ruff and mypy |
| `make fmt` | Format code |

`make up` must be running before `make test`, because tests use a real PostgreSQL database.

## Configuration

All settings come from environment variables, documented in [`.env.example`](.env.example). The application refuses to start if a required value is missing. Outside `ENVIRONMENT=dev` it also refuses placeholder or short secrets. When `ENVIRONMENT` is unset, the application applies the production rules, and production requires `REQUIRE_STAFF_2FA=true`.
