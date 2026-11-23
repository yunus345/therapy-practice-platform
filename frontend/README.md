# Frontend

The Next.js frontend for Seans uses the App Router, strict TypeScript, Tailwind CSS,
shadcn/ui, next-intl, TanStack Query and Zod.

## Local development

```sh
pnpm install
pnpm dev
```

The application runs at `http://localhost:3000`. Set `NEXT_PUBLIC_API_BASE_URL` to
the FastAPI origin; the default is `http://localhost:8000`.

## Validation

```sh
pnpm lint
pnpm format:check
pnpm test
pnpm build
pnpm test:e2e
```
