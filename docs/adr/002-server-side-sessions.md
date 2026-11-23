# ADR-002: Server-side sessions instead of JWT

**Status:** Accepted (2026-11-02)

## Context

Staff and clients sign in from browsers to see personal data. A stolen or obsolete credential must stop working at once: on logout, password reset, account deactivation, or when a user revokes a device.

## Decision

Use server-side sessions. The browser holds an opaque random token in an `httpOnly`, `Secure`, `SameSite=Lax` cookie. The `auth_sessions` table stores only a hash of the token, plus expiry, revocation time and MFA state.

## Alternatives

- **JWT access tokens:** rejected. They cannot be revoked before they expire without a server-side denylist, which removes their main benefit. Claims also go stale when a role changes.
- **JWT in `localStorage`:** rejected. Any script injection could read the token.

## Consequences

- Every authenticated request reads the database. This is acceptable at this scale, and an index on `token_hash` keeps it cheap.
- Revocation is one `UPDATE`, and a user can list and end their own sessions.
- A database leak does not expose usable tokens, because only hashes are stored.
- Cookies require CSRF protection, and the frontend and API must share a registrable domain in production for `SameSite=Lax` to work.
