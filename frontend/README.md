# Frontend

The Next.js frontend for Seans uses the App Router, strict TypeScript, Tailwind CSS,
shadcn/ui, next-intl, TanStack Query and Zod.

## Local development

Use Node.js from `.nvmrc` and pnpm through Corepack (see the root README):

```sh
corepack pnpm install --frozen-lockfile
corepack pnpm dev
```

The application runs at `http://localhost:3000`. Frontend settings are documented in
[`.env.example`](.env.example); copy it to `.env.local` to change them. Without it,
`NEXT_PUBLIC_API_BASE_URL` defaults to `http://localhost:8000`.

## Validation

```sh
corepack pnpm lint
corepack pnpm format:check
corepack pnpm typecheck
corepack pnpm test
corepack pnpm build
corepack pnpm test:e2e
```
