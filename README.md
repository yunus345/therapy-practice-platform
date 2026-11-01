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
| `make test` | Run backend tests against the test database |
| `make lint` | Run ruff and mypy |
| `make fmt` | Format code |

`make up` must be running before `make test`, because tests use a real PostgreSQL database.

## Configuration

All settings come from environment variables, documented in [`.env.example`](.env.example). The application refuses to start if a required value is missing. Outside `ENVIRONMENT=dev` it also refuses placeholder or short secrets. When `ENVIRONMENT` is unset, the application applies the production rules, and production requires `REQUIRE_STAFF_2FA=true`.
