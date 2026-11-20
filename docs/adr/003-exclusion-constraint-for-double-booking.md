# ADR-003: Double booking prevented by an exclusion constraint

**Status:** Accepted (2026-11-02)

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

- A booking locks the psychologist's profile row `FOR SHARE`, so bookings run in parallel and the constraint decides between them. When `buffer_minutes > 0` it locks `FOR UPDATE` instead, because the constraint does not know about buffers. If a buffer appears while a request waits for its shared lock, it locks again exclusively.
- Creating or changing time off locks the profile `FOR UPDATE`. It waits for bookings in flight, and later bookings see the new period.
- Staff bookings always take the shared lock, because they may ignore the buffer.
- A reschedule locks the appointment row first and the profile second. No writer takes them in the other order.
- The insert runs in a savepoint. An exclusion violation, or a deadlock between two requests upgrading their locks, becomes `409 SLOT_TAKEN`; the request can be retried.

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
