# PVR New Movie Alert

Get daily Telegram alerts when new movies appear on PVR Cinemas. Runs for free on GitHub Actions.

## How it works

- Runs daily on a GitHub Actions cron schedule
- Fetches now-showing movies from PVR's API for your city
- Compares against the previous snapshot to detect new additions
- Filters by language, genre, or certificate if configured
- Sends a Telegram alert for matching new movies

## Setup

### 1. Fork this repo

### 2. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram and send `/newbot`
2. Copy the bot token
3. Message [@userinfobot](https://t.me/userinfobot) to get your chat ID
4. Open your new bot in Telegram and send `/start`

### 3. Get a scrape.do token

Sign up at [scrape.do](https://scrape.do) (free tier is sufficient) and copy your API token.

### 4. Add GitHub secrets

Go to your repo **Settings** > **Secrets and variables** > **Actions** > **Secrets**:

| Secret | Description |
|--------|-------------|
| `SCRAPE_DO_TOKEN` | Your scrape.do API token |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

### 5. Configure filters (optional)

Go to **Settings** > **Secrets and variables** > **Actions** > **Variables**:

| Variable | Default | Description |
|----------|---------|-------------|
| `PVR_CITY` | `Chennai` | City to check (see [PVR website](https://www.pvrcinemas.com) for options) |
| `PVR_LANGUAGES` | *(all)* | Comma-separated, e.g. `English, Tamil` |
| `PVR_GENRES` | *(all)* | Comma-separated, e.g. `Action, Thriller` |
| `PVR_CERTIFICATES` | *(all)* | Comma-separated, e.g. `U, UA 16+` |

Filter logic:
- Within a filter: **OR** (movie matches at least one value)
- Across filters: **AND** (all configured filters must pass)
- If a filter is not set, all movies pass through on that dimension

### 6. Run

The workflow runs automatically every day at 11:30 AM IST. You can also trigger it manually from the **Actions** tab.
