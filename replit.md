# Robin Token Alert

A Telegram bot that monitors new token launches on Robinhood Chain DEXs every ~2 minutes, runs automated safety checks (honeypot, liquidity, LP lock, holder concentration), and sends alerts with action buttons to a Telegram channel.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)
- **Robin Token Alert**: Python serverless functions on Vercel, Upstash Redis, GitHub Actions scheduler

## Where things live

- `artifacts/robin-token-alert/` — the Python Telegram bot project (Vercel-deployable)
  - `api/scan.py` — scheduled scan endpoint (called by GitHub Actions every 2 min)
  - `api/bot.py` — Telegram webhook command handler
  - `lib/` — redis_client, dexscreener, safety, telegram helpers
  - `.github/workflows/scan-cron.yml` — 2-minute GitHub Actions schedule
  - `README.md` — full setup guide (env vars, webhook registration, etc.)

## Architecture decisions

- Two serverless endpoints instead of a long-running process — keeps costs at zero on Vercel free tier
- GitHub Actions as the scheduler (not Vercel Cron) — free unlimited minutes on public repos
- Upstash Redis REST API (not a persistent connection) — safe for serverless cold starts
- Only two Redis keys: `alerted_tokens` (SET) and `scanning_enabled` (STRING)
- Repo must be PUBLIC for free unlimited GitHub Actions minutes

## Product

Users get near-real-time Telegram alerts for new Robinhood Chain token launches that pass automated safety gates. Each alert shows token details, a check-by-check safety breakdown, and inline buttons to view on DexScreener or buy via Maestro.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- The GitHub repo MUST be public for free unlimited Actions minutes
- `SCAN_SECRET` must match in both Vercel env vars and GitHub Actions secrets
- Register the Telegram webhook manually after first deploy (see README)
- `GOPLUS_CHAIN_ID` defaults to `42161` (Arbitrum) — update if Robinhood Chain has its own GoPlus chain ID

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
- See `artifacts/robin-token-alert/README.md` for full deployment instructions
