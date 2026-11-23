# Frontend

The Next.js frontend for Seans uses the App Router, strict TypeScript, Tailwind CSS,
shadcn/ui, next-intl, TanStack Query and Zod.

## Local development

Use Node.js from `.nvmrc` and pnpm through Corepack (see the root README):

```sh
corepack pnpm install --frozen-lockfile
corepack pnpm dev
```

The application runs at `http://localhost:3000`. Set `NEXT_PUBLIC_API_BASE_URL` to
the FastAPI origin; the default is `http://localhost:8000`.

## Validation

```sh
corepack pnpm lint
corepack pnpm format:check
corepack pnpm typecheck
corepack pnpm test
corepack pnpm build
corepack pnpm test:e2e
```
