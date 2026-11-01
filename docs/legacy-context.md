# Legacy context

A first prototype of this idea was built a year ago. Its code is **not** a reference and must not be used. This file is the only input taken from it: the domain concepts worth keeping.

## Roles
- **Psychologist / admin**: logged in to a management panel.
- **Client** (called "danışan" in the UI; avoid "hasta"/patient in user-facing text): logged in to a personal panel.

## Screens the prototype had
**Psychologist panel**
- Dashboard: today's appointment count, this month's appointment count, total clients, this month's collected vs. outstanding payments.
- Client list, client detail, add client.
- Add appointment, edit appointment, daily appointment calendar.
- Payment list.
- Statistics: appointments per month, distribution by service type, revenue per month, weekday × hour heatmap of appointment density (Mon–Fri, 09:00–17:00).

**Client panel**
- Upcoming appointments with service type.
- Past payments with a chart.
- Appointment history.

## Data concepts
- Psychologists, clients, services (type + price), appointments (client, psychologist, service, date-time, status), payments linked to appointments (amount, date, status: paid / pending / partial), session notes linked to appointments.

## Known problems not to repeat
- No real authentication for clients; hardcoded IDs.
- Plain-text passwords; credentials written to logs.
- Unprotected API endpoints exposing all client data.
- No appointment end time, so overlaps could not be detected.
- Prices stored as text; free-text status values.
- Edits saved to browser storage instead of the database.
