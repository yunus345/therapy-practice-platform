# ADR-004: Audit log immutability

**Status:** Accepted (2026-11-02)

## Context

The audit log must stay trustworthy even if the application is compromised. The phase spec requires that the application database role cannot update or delete its rows.

## Decision

Enforce this in two layers in the database:

1. **Privileges:** the application connects as a restricted role. Migration `0006` revokes `UPDATE`, `DELETE` and `TRUNCATE` on `audit_logs` from it. The role does not own any table, so it cannot alter or disable anything.
2. **Triggers:** `trg_audit_logs_append_only` and `trg_audit_logs_no_truncate` reject changes from every role, including the owner.

Migrations run as a separate owner role (`MIGRATION_DATABASE_URL`). The runtime uses `DATABASE_URL`, which must never point to the owner. `appointment_status_history` also loses these privileges, because it is an append-only record too.

## Alternatives

- **Application-level rule only:** rejected. A bug or an attacker with the runtime credentials could rewrite history.
- **Privileges only:** rejected. The trigger also stops accidental changes in manual owner sessions.
- **External log store:** deferred. It gives stronger tamper resistance, but this stage does not need it.

## Consequences

- A superuser can still disable the triggers. In production the owner credential must stay out of the application environment.
- Retention needs a deliberate maintenance path, because normal deletes fail.
- The foreign key to `users` blocks deletion, so a user with audit entries cannot be hard-deleted.
