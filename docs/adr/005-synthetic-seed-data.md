# ADR-005: Synthetic seed data with documented generating rules

**Status:** Accepted (2026-11-20)

## Context

Development and demos need realistic data: about 80 clients, six months of past appointments and four weeks of upcoming ones. Phase 5 will train a no-show model on the same data. If the patterns in the data are not written down, nobody can tell whether such a model found something real or fitted noise.

## Decision

- `make seed` runs `python -m app.seed`. It refuses to run unless `ENVIRONMENT=dev`, and it refuses a database that already has users, so it can never mix with real data.
- The data is built in memory from one random generator with a fixed seed and Faker's `tr_TR` locale. The same day and the same Faker version give the same data, identifiers included. All dates are relative to the day the seed runs, so upcoming appointments stay upcoming; `--today YYYY-MM-DD` rebuilds the data of another day.
- Rows are written as ORM models through the restricted application role, in one transaction. Services cannot be used: they reject appointments in the past by design. The database constraints still check every row, including the exclusion constraint of ADR-003.
- `app/seed/behavior.py` holds every rule that decides how a session turns out: early and late cancellation, no-show and dropout. Each outcome has a log-odds score built from documented effects: booking lead time, prior no-shows and late cancellations, first session, weekday, hour, online session and online booking. Changing a rule means updating this ADR.
- Status history and KVKK disclosure records are written as the application would write them. Audit log entries are not: the audit log records real actions only.
- Every demo account shares one password, printed at the end of the run. Every email address uses `example.com`.

## Alternatives

- **Calling the API or the services:** rejected. Past appointments and their outcomes cannot be created through rules that protect the present.
- **A fixed data file:** rejected. Its dates would go stale, and the generating rules would be hidden in the data.
- **Deleting existing data before seeding:** rejected. A mistake in the environment would destroy real data. Starting over is a separate, explicit step.

## Consequences

- A no-show model can be checked against known effects: it should recover them, and anything more it claims is noise.
- The demo password is public. The environment check is what keeps it out of production.
- A Faker upgrade can change names and contact details, but not the structure of the data.
- Starting over means recreating the schema, which deletes all local data.
