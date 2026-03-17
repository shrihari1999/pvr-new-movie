# PVR New Movie Alert

A Cloudflare Worker that checks PVR Cinemas for new movies daily and sends alerts via Telegram.

## How it works

- Runs on a cron schedule (daily at 6 AM UTC / 11:30 AM IST)
- Fetches now-showing movies from PVR's API for a configured city
- Compares against the previous snapshot stored in Cloudflare KV
- Sends a Telegram alert for any new movies that match your filters
- Gradually builds a knowledge base of cities, languages, genres, and certificates

## Setup

### 1. Create a KV namespace

In the Cloudflare dashboard: **Storage & Databases** > **KV** > **Create a namespace** > Name it `PVR_DATA`

Update the KV namespace ID in `wrangler.toml`.

### 2. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the bot token
4. Message [@userinfobot](https://t.me/userinfobot) to get your chat ID
5. Start a chat with your new bot and send `/start`

### 3. Configure secrets

In the Cloudflare dashboard: **Workers & Pages** > your worker > **Settings** > **Variables and Secrets**

| Name | Type | Description |
|------|------|-------------|
| `TELEGRAM_BOT_TOKEN` | Secret | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Secret | Your Telegram chat ID |
| `API_KEY` | Secret | Any strong random string (protects the HTTP trigger) |

### 4. Configure filters

Edit `wrangler.toml` to set your preferences:

```toml
[vars]
PVR_CITY = "Chennai"           # City to check (see PVR website for options)
PVR_LANGUAGES = "English"      # Comma-separated, or empty for all
PVR_GENRES = ""                # Comma-separated, or empty for all
PVR_CERTIFICATES = ""          # Comma-separated, or empty for all
```

Filter logic:
- Within a filter: **OR** (movie matches at least one value)
- Across filters: **AND** (all configured filters must pass)

### 5. Deploy

Connect your GitHub repo in the Cloudflare dashboard: **Workers & Pages** > your worker > **Settings** > **Build** > connect repo. Every push to `master` auto-deploys.

### Manual trigger

```bash
curl -X GET https://your-worker.workers.dev/ -H "x-api-key: YOUR_API_KEY"
```

## Knowledge base

The worker automatically discovers and stores all unique values in KV:

| KV Key | Contents |
|--------|----------|
| `known_cities` | All available PVR cities |
| `known_languages` | All movie languages seen |
| `known_genres` | All genres seen |
| `known_certs` | All certificate ratings seen |
| `last_movies` | Snapshot from the last run |
