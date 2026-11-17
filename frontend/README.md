# Document Copilot frontend

Vite + React + TypeScript single-page app. The UI uses Tailwind CSS and shadcn/ui components. Supabase handles email/password sign-in; the frontend sends the returned access token to the FastAPI backend at `/api/auth/me`.

## Setup

Install dependencies with pnpm and copy `.env.example` to `.env`. Set the API URL and Supabase project URL and anon key in `.env`:

```sh
pnpm install
cp .env.example .env
pnpm dev
```

The backend API must be running and reachable at `VITE_API_BASE_URL`.

## Scripts

- `pnpm dev` — start the Vite development server
- `pnpm build` — type-check and build for production
- `pnpm lint` — run ESLint
- `pnpm preview` — serve the production build locally
