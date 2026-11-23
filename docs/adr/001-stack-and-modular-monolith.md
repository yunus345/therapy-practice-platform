# ADR-001: Stack and modular monolith

**Status:** Accepted (2026-11-02)

## Context

Seans handles personal data and possibly health-related data for a small practice. One person maintains it occasionally. A later phase will add analytics and an AI assistant that must reuse the same business rules and authorization.

## Decision

- Use a separated frontend (Next.js) and a backend API (FastAPI, async SQLAlchemy, Alembic) on PostgreSQL 17.
- Structure the backend as a modular monolith. Each module has `router`, `schemas`, `service`, `repository` and `models`. Modules call each other only through services.
- Keep authorization in the service layer, so that a service is safe to call without a router in front of it.
- Use two database roles: an owner for migrations and a restricted role for the application.

## Alternatives

- **Microservices:** rejected. They add deployment and consistency costs that one maintainer and one practice do not justify.
- **Full-stack Next.js:** rejected. Python keeps the backend close to the planned data science and AI work.
- **Unstructured monolith:** rejected. The layer rules are what let the future AI agent call services as tools safely.

## Consequences

- One deployable backend and one database keep transactions simple across modules.
- Module boundaries depend on discipline and review, because Python does not enforce them.
- The two-role setup needs a bootstrap step in every environment, because a migration cannot own the password of the application role.
