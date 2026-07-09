# Robin Token Alert

A Telegram bot that continuously monitors new token launches on Robinhood Chain DEXs, runs automated safety checks, and sends alerts with action buttons to a Telegram channel.

## How it works

Two serverless Python endpoints on Vercel, triggered by GitHub Actions on a 2-minute schedule:

- **`/api/scan`** — Called every 2 minutes. Discovers all new tokens on Robinhood Chain via DexScreener, runs safety checks (honeypot, liquidity, LP lock, holder concentration), and sends Telegram alerts for tokens that pass (PASS or CAUTION status).
- **`/api/bot`** — Telegram webhook. Handles `/start`, `/stop`, `/status`, `/scan`, `/help` commands.

> ⚠️ **This repo must be PUBLIC.** GitHub Actions minutes are free and unlimited on public repos. On private repos, a 2-minute cron would burn through the 2,000 free minutes/month in less than 2 days.

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
| `SCAN_SECRET` | A secret string you choose — sent as `X-Scan-Secret` header by GitHub Actions to authenticate scan calls |
| `MAESTRO_BOT_USERNAME` | Maestro Telegram bot username (default: `maestro`) |
| `MAESTRO_DEEP_LINK_TEMPLATE` | Deep-link URL template. Default: `https://t.me/{bot_username}?start={contract_address}` |
| `MIN_LIQUIDITY_USD` | Minimum liquidity to pass (default: `3000`) |
| `GOPLUS_CHAIN_ID` | GoPlus chain ID for Robinhood Chain (default: `42161` for Arbitrum; update if RBN has its own ID) |

Set these in **GitHub Actions** (Settings → Secrets and variables → Actions → New repository secret):

| Secret | Description |
|---|---|
| `VERCEL_APP_URL` | Your deployed Vercel URL, e.g. `https://robin-token-alert.vercel.app` |
| `SCAN_SECRET` | Same value as `SCAN_SECRET` in Vercel — the workflow sends this header with every scan call |

---

## Setup steps

### 1. Create a Telegram bot
1. Message [@BotFather](https://t.me/BotFather) → `/newbot`
2. Copy the bot token → set as `TELEGRAM_BOT_TOKEN`
3. Find your Telegram user ID (e.g. via [@userinfobot](https://t.me/userinfobot)) → set as `OWNER_TELEGRAM_ID`
4. Create a channel and add your bot as an admin, then get the chat ID → set as `TELEGRAM_CHAT_ID`

### 2. Create Upstash Redis
1. Sign up at [upstash.com](https://upstash.com) (free tier is sufficient)
2. Create a new Redis database
3. Copy the REST URL and REST Token → set as `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`

### 3. Deploy to Vercel
1. Push this repo to a **public** GitHub repository
2. Import the repo into [Vercel](https://vercel.com)
3. Set all environment variables listed above
4. Deploy — note your deployment URL (e.g. `https://robin-token-alert.vercel.app`)

### 4. Register the Telegram webhook
After deploying, run this once to point Telegram at your `/api/bot` endpoint:

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://your-app.vercel.app/api/bot"
```

You should see `{"ok":true,"result":true,"description":"Webhook was set"}`.

### 5. Set GitHub Actions secrets
In your GitHub repo → Settings → Secrets and variables → Actions:
- Add `VERCEL_APP_URL` = `https://your-app.vercel.app`
- Add `SCAN_SECRET` = the same secret string you put in Vercel

### 6. Test it
- Push or manually trigger the `Scan for New Tokens` workflow from the Actions tab
- Message your bot `/status` to confirm connectivity
- Message your bot `/scan 0x<any_contract_address>` to test an on-demand safety check

---

## Safety checks

Each token is evaluated on four criteria:

| Check | Pass | Caution | Fail |
|---|---|---|---|
| **Honeypot** | No honeypot, taxes ≤10% | Buy or sell tax >10% | Sell simulation fails |
| **Liquidity** | ≥ $3,000 USD | Below minimum | — |
| **LP lock** | ≥80% locked/burned | 50–80% locked | — |
| **Holder concentration** | Top 10 ≤60% of supply | Top 10 >60% | — |

- **FAIL** → no alert sent, address added to `alerted_tokens` to avoid rechecking
- **CAUTION** → alert sent, flagged checks listed explicitly
- **PASS** → alert sent

Every alert includes: _"Not financial advice. Automated checks catch known scam patterns only — always verify independently before trading."_

---

## Project structure

```
api/
  scan.py          # Scheduled discovery + safety + alert endpoint
  bot.py           # Telegram webhook command handler
lib/
  redis_client.py  # Upstash Redis connection and helpers
  dexscreener.py   # DexScreener API wrapper
  safety.py        # Honeypot/liquidity/holder check functions
  telegram.py      # Message formatting and send functions
.github/
  workflows/
    scan-cron.yml  # GitHub Actions schedule (every 2 min)
vercel.json        # Vercel routing config
requirements.txt   # Python dependencies
```

---

## Redis state (only two keys)

| Key | Type | Purpose |
|---|---|---|
| `alerted_tokens` | SET | Contract addresses already alerted — prevents duplicate alerts |
| `scanning_enabled` | STRING | `"true"` or `"false"` — controlled by `/start` and `/stop` commands |

No SQL database, no ORM, no other state.
