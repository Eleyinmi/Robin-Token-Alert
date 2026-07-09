# Robin Token Alert

A Telegram bot that monitors new token launches on Robinhood Chain DEXs every ~2 minutes, runs automated safety checks (honeypot, liquidity, LP lock, holder concentration), and sends alerts with action buttons to a Telegram channel.

## How it works

Two serverless Python endpoints on Vercel, triggered by GitHub Actions on a 2-minute schedule:

- **`/api/scan`** — Called every 2 minutes. Discovers all new tokens on Robinhood Chain via DexScreener, runs safety checks, and sends Telegram alerts for tokens that pass.
- **`/api/bot`** — Telegram webhook. Handles `/start`, `/stop`, `/status`, `/scan`, `/help` commands.

> ⚠️ **This repo must be PUBLIC.** GitHub Actions minutes are free and unlimited on public repos.

---

## Required environment variables

Set these in **Vercel** (Project → Settings → Environment Variables):

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Channel or chat ID to send alerts to (e.g. `-1001234567890`) |
| `OWNER_TELEGRAM_ID` | Your Telegram user ID — only this user can use `/start` and `/stop` |
| `UPSTASH_REDIS_REST_URL` | Upstash Redis REST URL (from Upstash console) |
| `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis REST token (from Upstash console) |
| `SCAN_SECRET` | A secret string you choose — sent as `X-Scan-Secret` header by GitHub Actions |
| `MAESTRO_BOT_USERNAME` | Maestro Telegram bot username (default: `maestro`) |
| `MIN_LIQUIDITY_USD` | Minimum liquidity to pass (default: `3000`) |
| `GOPLUS_CHAIN_ID` | GoPlus chain ID (default: `42161` for Arbitrum) |

Set these in **GitHub Actions** (Settings → Secrets and variables → Actions):

| Secret | Description |
|---|---|
| `VERCEL_APP_URL` | Your deployed Vercel URL, e.g. `https://robin-token-alert.vercel.app` |
| `SCAN_SECRET` | Same value as `SCAN_SECRET` in Vercel |

---

## Setup steps

1. Create a Telegram bot via @BotFather → save token as `TELEGRAM_BOT_TOKEN`
2. Get your Telegram user ID via @userinfobot → save as `OWNER_TELEGRAM_ID`
3. Create a channel, add bot as admin, get chat ID → save as `TELEGRAM_CHAT_ID`
4. Create Upstash Redis database → save REST URL and token
5. Deploy to Vercel — set all environment variables before deploying
6. Register the Telegram webhook:
   ```
   https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-app.vercel.app/api/bot
   ```
7. Add `VERCEL_APP_URL` and `SCAN_SECRET` as GitHub Actions secrets

---

## Safety checks

| Check | Pass | Caution |
|---|---|---|
| Honeypot | No honeypot, taxes ≤10% | Buy or sell tax >10% |
| Liquidity | ≥ $3,000 USD | Below minimum |
| LP lock | ≥80% locked/burned | 50–80% locked |
| Holder concentration | Top 10 ≤60% of supply | Top 10 >60% |

FAIL tokens are silently skipped. CAUTION tokens alert with flags listed. Every alert includes a DYOR disclaimer.

---

## Project structure

```
api/
  scan.py       # Scheduled discovery + safety + alert endpoint
  bot.py        # Telegram webhook command handler
bot_lib/
  redis_client.py
  dexscreener.py
  safety.py
  telegram.py
.github/
  workflows/
    scan-cron.yml  # GitHub Actions schedule (every 2 min)
vercel.json
requirements.txt
```
