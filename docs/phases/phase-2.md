# Phase 2: Public website and psychologist panel

Read `CLAUDE.md`, `AGENTS.md` and `PROGRESS.md` first. All rules there apply.

This spec was written by the owner and **replaces** the earlier `phase-2.md` that an agent wrote on its own. The work already done under that earlier spec (frontend foundation, account screens, client portal) is the starting point. Some of it is kept, some is reworked, and the client account screens are removed.

## Goal

A public website for one psychologist where visitors read about the practice and book an appointment **without creating an account**, and a psychologist panel where the psychologist runs the practice and manages the site content without touching code. The visual design is chosen by the owner before new screens are built.

## Owner decisions behind this spec

1. **No client accounts.** Clients never register or log in and have no logged-in area. Only `admin` and `psychologist` users exist.
2. **Booking without an account:** the client picks a slot, enters name, phone and email, accepts the KVKK disclosure, confirms with a one-time code sent by email, and receives a confirmation email with a private "manage my booking" link. The psychologist is notified of every new booking and every client cancellation.
3. **Booking mode is a panel setting:** `online` (the flow above), `whatsapp` (the booking button opens WhatsApp with a prefilled message) or `both`. Switching modes needs no code change.
4. **WhatsApp scope:** only a `https://wa.me/<number>?text=<message>` link. The WhatsApp Business API (sending messages from the system) is out of scope.
5. **Verification by email code only.** No SMS in this phase.
6. **The psychologist manages the site content** from the panel: profile photo, texts, contact details, social links, FAQ, services and prices, booking rules and booking mode.
7. **The psychologist manages their own services and prices.** Today only `admin` can; this changes.
8. **Design is decided by the owner** in a dedicated milestone before the public pages and the panel are built.

## How to work

1. Read this spec end to end and list your questions.
2. Present a plan **for M0 only**, including the order of commits and any dependency you want to add or remove.
3. Wait for approval. After each milestone: lint, tests, commit, update `PROGRESS.md`, give the Turkish summary, and **stop**.
4. Before each later milestone, present that milestone's plan and wait again.

## M0: Stabilization

The audit of the earlier work found uncommitted work, iCloud duplicates and several defects. Fix the foundation before building anything new. Do **not** spend effort on the client account screens (login for clients, registration, email verification for clients, `/randevularim`, `/hesabim`); they are removed in M1.

**Repository and history**
- The owner moves the repository out of the iCloud-synced Desktop (target: `~/Projeler/therapy-practice-platform`). After the move, recreate environments, because they contain absolute paths: `uv sync` for the backend, a clean `pnpm install` for the frontend.
- Delete the iCloud duplicates (`frontend/src/app/page 2.tsx`, `frontend/README 2.md`, `backend/.coverage 2`) and any `* 2` copies inside `node_modules` (a clean install removes those). Add a check (pre-commit or CI) that fails when a file or folder name ends in ` 2` or ` 2.<ext>`.
- Commit the existing uncommitted work **as it is**, in separate commits that match what was done: Phase 1 M7, then Phase 2 (old) M1, M2, M3. Messages must describe the content honestly.
- With the owner's approval, push the `phase-1` branch and confirm CI runs green. Then propose how to bring the work to `main` (merge or pull request) and wait for approval.

**Backend fixes**
- **Deadlock handling** (`users/repository.py`, `users/service.py`, `scheduling/service.py`): either cover it with a test that actually reproduces the lock conflict, or replace it with a simpler design. Use the project's normal error pattern (raise an `AppError`), not a marker object with a cast. `PROFILE_LOCK_CONFLICT` must not leak from time off or staff reschedule paths. Remove the extra savepoint if it is not needed. Explain the choice in ADR-003 or a new ADR.
- **Strict KVKK booleans:** move `StrictTrue` and `require_true` into `app/core/schemas.py` and restore `const: true` in the OpenAPI schema.
- **Correct the record:** the M7 log entry says the acting psychologist is written to the audit entry, but for online bookings the actor is the client. Fix the log entry and the outdated "No actor here" comment.

**Frontend and tooling fixes**
- **Generated API types:** generate TypeScript types from the backend OpenAPI schema (propose the tool) and replace the hand-written ones. CI fails if the generated types are out of date.
- **Error-code contract:** the backend exposes its error codes as one enum that appears in the OpenAPI schema. A test fails when the frontend maps a code the backend does not have, or when a code the frontend can receive has no Turkish message.
- **Dependencies:** remove `@testing-library/react` and `eslint-config-prettier` if still unused. Keep the `cn` package (shadcn/ui's official class-name helper, `shadcn-ui/cn`) declared explicitly in `package.json`; `lib/utils` at most re-exports it. Remove `clsx` and `tailwind-merge` if nothing else uses them. These removals are approved by this spec. Any addition still needs approval.
- **CI:** pin `pnpm/action-setup` and `actions/setup-node` to full versions like the other actions. Run Playwright in CI against a real backend and database.
- **Environment:** move `NEXT_PUBLIC_API_BASE_URL` to `frontend/.env.example`; remove it from the root `.env.example`.
- **Makefile and README:** `make help` lists every target, including names with hyphens. Do not run `npx --yes`; use a pinned pnpm through Corepack or an explicit, documented install. Pin the Node version (`.nvmrc` or `engines`) to the one CI uses, and list Node and pnpm in the README prerequisites.
- **`PROGRESS.md`:** keep the newest log entry on top.

**Not in M0:** Y3 (error-code mappings) and Y4 (hardcoded Turkish strings) for screens that are removed in M1. They are fixed for the remaining screens at the end of M1.

## M1: Booking without accounts

**Remove client accounts (backend)**
- Remove client self-registration, client email verification, client login, `GET /me/appointments` and the session-based client cancel and reschedule endpoints.
- Remove the `client` role. Write a reversible migration that first verifies no `client` users remain. Update the seed so it creates no client accounts.
- Decide whether `clients.user_id` is dropped or kept, and justify it in ADR-006.
- The login, password reset, 2FA and session endpoints become staff-only.

**Booking requests**
- `POST /api/v1/public/psychologists/{slug}/booking-requests` with service, start time, first and last name, email, phone and the KVKK acknowledgement (with the disclosure version). It validates the slot and the booking mode, stores a pending request and emails a one-time code.
- `POST /api/v1/public/booking-requests/{id}/verify` with the code. It re-checks the slot, then in one transaction: finds or creates the client record, creates the appointment (`confirmed` if the psychologist auto-confirms, otherwise `requested`), writes the status history, the consent record and the audit entry, and creates the manage token. Then it emails the client and the psychologist.
- Code rules: 6 digits, stored hashed, valid for 15 minutes, at most 5 attempts, at most a few pending requests per email at a time. Expired and used requests cannot be reused.
- The slot is not held while the client waits for the code. If it was taken in the meantime, verification returns `SLOT_TAKEN` and the client picks another slot.
- **No enumeration:** responses never reveal whether the email or phone already belongs to a client.
- **Matching existing clients:** match only by normalized email within the psychologist's clients. Never overwrite stored client details with submitted ones. If they differ, keep the submitted values on the booking request so the psychologist can review them.
- **Abuse protection:** rate limits per IP and per email. Propose whether to add a privacy-friendly challenge such as Cloudflare Turnstile (a new external service and dependency needs approval). Development and tests must work without it.

**Managing a booking**
- `GET /api/v1/public/bookings/{token}` returns only what the client needs: date and time, service, delivery mode, status, psychologist name, and whether cancel or reschedule is still possible.
- `POST .../cancel` within the cancellation window; `POST .../reschedule` to a new free slot, keeping the existing reschedule limits.
- Token rules: at least 32 random bytes, stored hashed, valid for one appointment, expiring when the appointment ends. A reschedule issues a new token for the new appointment and invalidates the old one.

**Notifications** (existing email service and templates, Turkish)
- To the client: verification code; booking received (`requested`); booking confirmed; cancelled (by client or psychologist); rescheduled.
- To the psychologist: new booking (with a panel link, without health information); cancelled or rescheduled by the client.
- Approving or declining a `requested` appointment in the panel emails the client.
- Emails never contain clinical information. The manage link appears only in client emails.

**Booking mode** (stored on the psychologist profile, editable via `/staff/profile`)
- `booking_mode`: `online`, `whatsapp` or `both`; `whatsapp_number` (E.164); `whatsapp_message` (prefilled text with a sensible Turkish default).
- In `whatsapp` mode, the public booking-request endpoints return `BOOKING_DISABLED`. The public profile response tells the frontend which buttons to show and gives the WhatsApp link data.

**Frontend in M1**
- Delete the client registration, client login, client email verification, `/randevularim` and `/hesabim` screens, their routes and their proxy protection.
- Move staff login under the panel (for example `/panel/giris`); it is not linked from public pages.
- Keep the public profile and slot selection screens, but change their booking form to the new flow. Visual polish waits for M3.
- Then fix Y3 and Y4 for all remaining screens: every user-facing string comes from `messages/tr.json`, and every error code is mapped.

**Docs and tests**
- ADR-006: client accounts removed, booking without accounts, manage tokens, matching rules.
- Tests: the whole booking flow, wrong and expired codes, attempt limits, the slot taken during verification, no enumeration, token scope (token A cannot read or change appointment B), the cancellation window, reschedule and token rotation, `whatsapp` mode blocking online booking, emails sent with the right content, and the updated authorization matrix.

## M2: Site settings (backend)

**Profile photo**
- Upload, replace and remove through the panel API. Accept JPEG, PNG and WebP up to 5 MB, checked by decoding the image. Re-encode to a web format in a few sizes, strip all metadata, store under random names behind a storage interface (local volume now; S3-compatible storage possible later) and serve with cache headers. Delete replaced files. Never accept SVG. Image processing needs a dependency (for example Pillow); propose it.

**Public content fields** (plain text only; no HTML or Markdown that could inject markup)
- Short tagline, about text (the existing `bio`), areas of focus (short list), education and certifications (list), FAQ entries (ordered question/answer pairs).
- Contact: public phone (with a "show on site" switch, separate from the internal phone), public email, address text, map link (validated `https` URL), Instagram and LinkedIn URLs (validated).
- A "show prices on the site" switch.

**Services and prices**
- The psychologist can create, edit, deactivate and price the services they offer. Admin keeps full control. A psychologist cannot change a service that another psychologist also offers; they can only set their own price for it. Explain the rule in the plan.

**Public profile API**
- Extend the allow-list response with the new public fields, photo URLs, booking mode and WhatsApp link data. Hidden fields (internal phone, booking rules not meant for clients) stay out.

**Audit and tests**
- Audit entries for settings changes (field names only, no content). Tests for validation, upload limits and file-type spoofing, metadata removal, authorization, and the public allow-list.

## M3: Design direction (owner decision, no feature code)

- Prepare **two or three visual directions**. Each covers a color palette with contrast checked for WCAG AA, typography that renders Turkish characters well (ğ, ş, ı, İ, ç, ö, ü) and is self-hosted, spacing and corner style, component style and the tone of the copy.
- Show each direction on a development-only preview page with realistic Turkish content: the landing page hero, a service card, the slot picker, the booking form, the booking confirmation, and a panel table with a sidebar. Include mobile views.
- Propose the landing page structure (sections and their order) and the menu of the public site.
- Ask the owner about the product name (currently the working title "Seans") and the sender name and address of emails.
- **Stop and wait.** The owner chooses or mixes. Record the result in `docs/design.md` (tokens, typography, components, do's and don'ts), apply the tokens, and restyle the screens kept from earlier milestones.

## M4: Public website

**Pages**
- Home: hero, short introduction, services, how booking works, a few FAQ items, contact and a booking call to action.
- About, Services (prices shown only if the switch is on), FAQ, Contact. The map is a link, not an embedded map, unless the owner approves an embed.
- KVKK disclosure and cookie policy pages. Until a lawyer provides the text, they show a clearly marked placeholder, and the production configuration refuses to start while the placeholder is active.

**Booking flow (by booking mode)**
- `online` / `both`: service → day and slot → contact form with the KVKK checkbox linking to the disclosure → code entry → success page with what happens next.
- `whatsapp` / `both`: a booking button that opens the `wa.me` link with the prefilled message.
- A manage-booking page opened from the email link: see, cancel, reschedule.

**Site owner**
- The site serves one psychologist at the root. Propose how the site-owner profile is chosen (a setting or an environment variable). `/psikolog/[slug]` may remain.

**SEO and performance**
- Public pages are server-rendered with metadata, Open Graph data, canonical URLs, `lang="tr"`, `sitemap.xml`, `robots.txt` and schema.org structured data for the practice.
- The panel, booking-manage and verification pages are `noindex`.
- Optimized images and good Core Web Vitals on mobile.

**Privacy**
- No trackers, analytics or third-party embeds without approval. Fonts are self-hosted.

## M5: Psychologist panel

- **Shell:** staff login with 2FA, responsive navigation (the psychologist will often use a phone), account security (2FA, sessions, password). After login, return to the panel page that was requested, accepting only internal panel paths (no open redirect).
- **Dashboard:** today's and upcoming appointments, requests waiting for approval, simple counts. Real analytics come in Phase 5.
- **Appointments:** day and week calendar plus a list; approve or decline requests; mark completed or no-show; cancel with a reason; reschedule; create appointments for phone bookings.
- **Availability and time off** management.
- **Clients:** list and search, detail with appointment history, create, edit, soft delete. Differences between stored and submitted contact details from booking requests are shown for review. No clinical data (Phase 3).
- **Site settings:** profile and photo, public content, FAQ, contact and social links, services and prices, booking rules, booking mode and WhatsApp settings, with a link to preview the public site.
- **Admin:** a minimal area to manage staff accounts.

## M6: Quality and acceptance

- Playwright end-to-end tests in CI against the real backend and database:
  - online booking end to end, reading the code from Mailpit
  - cancel and reschedule through the manage link
  - `whatsapp` mode
  - staff login with 2FA and approving a request
  - a settings change and a photo upload appearing on the public site
- Unit and component tests for forms, formatting and error handling.
- Accessibility checks on the main public pages and the booking flow (propose the tool).
- Responsive checks at phone, tablet and desktop widths.
- Phase 2 closing report: what was built, decisions, deviations from this spec with reasons, known limitations, and suggestions for Phase 3.

## Acceptance criteria

- The repository is outside iCloud, all work is committed, `main` contains it, and CI is green, including Playwright.
- Generated API types and the error-code contract are enforced in CI.
- No client accounts exist anywhere in the code, the database or the UI.
- A visitor can book without an account, confirm by email code, and manage the booking through the link. The psychologist is notified.
- `whatsapp` and `both` modes work and can be switched from the panel.
- The psychologist can change the photo, texts, contact details, FAQ, services, prices and booking settings from the panel, and the public site reflects them.
- The design follows `docs/design.md`, which the owner approved.
- Public pages pass the SEO basics above; private pages are `noindex`.
- No hardcoded user-facing strings remain in components.
- ADR-006 exists and the README is up to date.

## Out of scope

Payments (manual payment records come in Phase 3), clinical notes, reports and analytics, AI features, SMS, the WhatsApp Business API, a background job system (the automatic cancellation of unanswered requests stays deferred), deployment, and the real KVKK texts (a lawyer writes them).
