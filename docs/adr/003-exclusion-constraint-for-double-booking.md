# ADR-003: Double booking prevented by an exclusion constraint

**Status:** Accepted (2026-11-02). Locks revised 2026-11-23.

## Context

Two clients can request the same slot at the same moment. If the application checks for a free slot and then inserts, both requests can pass the check before either insert is visible.

## Decision

Enforce the rule in PostgreSQL with `ex_appointments_no_overlap`:

```sql
EXCLUDE USING gist (
    psychologist_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
) WHERE (status IN ('requested', 'confirmed'))
```

The range is half-open, so one appointment can start exactly when another ends. Only `requested` and `confirmed` appointments hold a slot. The scheduling service translates the violation into `409 SLOT_TAKEN`. The `btree_gist` extension allows `uuid` equality in a GiST index.

### Locks around the constraint

The constraint covers overlaps only. Time off and buffers are checked in the service, so a booking and a change to the schedule must not interleave:

- An online booking, and the new slot of a client reschedule, locks the psychologist's profile row `FOR UPDATE`, whatever the buffer. The constraint does not know about buffers, and a lock taken once at full strength never has to be upgraded.
- Creating or changing time off locks the profile `FOR UPDATE`. It waits for bookings in flight, and later bookings see the new period.
- Staff bookings and staff reschedules take the shared lock (`FOR SHARE`), because they may ignore the buffer, and they never upgrade it.
- A reschedule locks the appointment row first and the profile second. No writer takes them in the other order.
- The insert runs in a savepoint. An exclusion violation, or a deadlock between two staff inserts waiting for each other's uncommitted rows in the exclusion index, becomes `409 SLOT_TAKEN`; the request can be retried.

### Revision 2026-11-23: no lock upgrades

Online bookings first took the shared lock and upgraded it to `FOR UPDATE` when a buffer appeared while they waited. Two requests upgrading at once deadlock, so Phase 1 M7 caught the deadlock in a savepoint and turned it into an error. That code had no test that reproduced the deadlock, and its error code could also surface from time off and staff reschedules.

Online bookings now always lock exclusively, so the upgrade, the savepoint around the lock and the extra error code are gone.

Trade-off: online bookings of one psychologist no longer run in parallel. Each waits for the one before it to commit, which takes milliseconds. With one practice and a handful of bookings a day this costs nothing measurable. If booking traffic ever grows enough for the wait to matter, revisit this together with buffers in the constraint.

## Alternatives

- **Check, then insert:** rejected. It has a race condition.
- **Unique constraint on the start time:** rejected. It misses partial overlaps between services of different lengths.
- **Serializable isolation:** rejected. It needs retry logic on every booking and protects nothing outside this application.
- **Per-psychologist advisory lock:** rejected as the main guarantee. It only works if all writers remember to take it.

## Consequences

- The guarantee holds for every writer, including future agents and manual SQL.
- An unconfirmed `requested` appointment holds its slot until someone acts on it.
- The constraint does not cover `time_off`, because it spans one table. The service layer checks time off.
- `buffer_minutes` is not part of the constraint. The slot calculation applies it, and staff bookings may ignore it.
