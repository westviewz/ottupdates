# 📺 OTT Update Bot

A Python bot that automatically posts **daily Indian OTT release updates** to a Telegram channel.

Every day it queries the **TMDB API** for movies and TV shows newly available on streaming platforms in India, checks for duplicates using **MongoDB**, and sends a formatted post to your **Telegram channel**.

> **Data source**: [TMDB (The Movie Database)](https://www.themoviedb.org/) — the only data source for this MVP.

---

## Table of Contents

1. [What the bot does](#what-the-bot-does)
2. [Example Telegram post](#example-telegram-post)
3. [Prerequisites](#prerequisites)
4. [Step 1 — TMDB API Key](#step-1--tmdb-api-key)
5. [Step 2 — Telegram Bot](#step-2--telegram-bot)
6. [Step 3 — Add bot to your channel](#step-3--add-bot-to-your-channel)
7. [Step 4 — Get your channel ID](#step-4--get-your-channel-id)
8. [Step 5 — MongoDB](#step-5--mongodb)
9. [Step 6 — Configure environment variables](#step-6--configure-environment-variables)
10. [Running locally](#running-locally)
11. [Testing manually](#testing-manually)
12. [How duplicate prevention works](#how-duplicate-prevention-works)
13. [Deploying on Railway](#deploying-on-railway)
14. [Configuring the Railway Cron Job](#configuring-the-railway-cron-job)
15. [IST → UTC conversion](#ist--utc-conversion)
16. [Project structure](#project-structure)
17. [Adding a new OTT provider](#adding-a-new-ott-provider)
18. [TMDB attribution](#tmdb-attribution)
19. [Known limitations](#known-limitations)

---

## What the bot does

1. Runs once a day as a Railway Cron Job (no always-on service).
2. Queries TMDB's `/discover/movie` and `/discover/tv` endpoints filtered by:
   - Indian watch region (`watch_region=IN`)
   - Your configured OTT providers
   - A date window around today (configurable lookback/lookahead)
3. Fetches per-title streaming provider data from TMDB.
4. Normalizes provider names (e.g. "Prime Video" → "Amazon Prime Video").
5. Checks MongoDB to skip titles already posted.
6. Formats and sends a single HTML-formatted post to your Telegram channel.
7. Records successfully posted titles in MongoDB.
8. Exits cleanly.

---

## Example Telegram post

```
🎬 OTT RELEASES — 10 SEPTEMBER 2026

🔴 NETFLIX

  • 🎬 The Movie Title
    🇮🇳 Hindi
    📅 10 Sep

  • 📺 A Web Series
    🌐 English
    📅 09 Sep


🟣 JIOHOTSTAR

  • 🎬 Malayalam Film
    🇮🇳 Malayalam
    📅 10 Sep

━━━━━━━━━━━━━━━━━━━━
🔥 TODAY'S COUNT
  🔴 Netflix • 2
  🟣 JioHotstar • 1

Data source: TMDB
```

---

## Prerequisites

| Requirement | Details |
|---|---|
| Python 3.11+ | For local runs |
| TMDB account | Free at themoviedb.org |
| Telegram account | For @BotFather and your channel |
| MongoDB Atlas | Free tier is sufficient |
| Railway account | Free hobby tier works |

---

## Step 1 — TMDB API Key

1. Create a free account at <https://www.themoviedb.org/signup>.
2. Go to **Settings → API** (<https://www.themoviedb.org/settings/api>).
3. Request an API key (choose "Developer").
4. Copy the **API Read Access Token** (the long JWT-style token — NOT the short API Key v3).
5. Set it as `TMDB_API_KEY` in your environment.

> ⚠️ Use the **API Read Access Token** (Bearer token), not the short v3 key.

---

## Step 2 — Telegram Bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts.
3. Copy the **bot token** (format: `123456789:ABCdef...`).
4. Set it as `TELEGRAM_BOT_TOKEN`.

---

## Step 3 — Add bot to your channel

1. Open your Telegram channel.
2. Go to **Manage Channel → Administrators → Add Administrator**.
3. Search for your bot by username.
4. Enable the **Post Messages** permission.
5. Save.

---

## Step 4 — Get your channel ID

**For public channels:**
Your channel ID is simply `@yourchannelusername`.

**For private channels:**

1. Forward any message from your private channel to **@userinfobot**.
2. It will reply with the channel's numeric ID (e.g. `-1001234567890`).

Alternatively:
1. Temporarily make the channel public, note the username, then make it private again.
2. Or use the Bot API: call `https://api.telegram.org/bot<TOKEN>/getUpdates` after the bot sends a message to the channel.

Set the result as `TELEGRAM_CHANNEL_ID`.

---

## Step 5 — MongoDB

### Option A: MongoDB Atlas (recommended)

1. Create a free cluster at <https://cloud.mongodb.com/>.
2. Create a **database user** (username + password — save these).
3. Go to **Network Access** → Add IP Address → **Allow Access from Anywhere** (for Railway).
4. Go to **Clusters → Connect → Connect your application**.
5. Copy the connection string. It looks like:
   ```
   mongodb+srv://<username>:<password>@cluster0.abc12.mongodb.net/
   ```
6. Replace `<username>` and `<password>` with your database user credentials.
7. Set as `MONGODB_URI`.

### Database and collection

The bot creates the database and collection automatically on first run.  
Default names:
- Database: `ott_update_bot`
- Collection: `posted_items`

Override with `MONGODB_DATABASE` and `MONGODB_COLLECTION` env vars.

---

## Step 6 — Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
# Required
TMDB_API_KEY=eyJhbGciOiJIUzI1NiJ9...        # Long Bearer token from TMDB
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
TELEGRAM_CHANNEL_ID=@yourchannel              # or -1001234567890
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/

# Optional (defaults shown)
MONGODB_DATABASE=ott_update_bot
MONGODB_COLLECTION=posted_items
TMDB_REGION=IN
OTT_PROVIDERS=Netflix,Amazon Prime Video,JioHotstar,ZEE5,Sony Pictures Networks India
LOOKBACK_DAYS=1
LOOKAHEAD_DAYS=1
POST_EMPTY_UPDATE=false
```

---

## Running locally

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd telegram-ott

# 2. Create and activate virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env file
cp .env.example .env
# Fill in the required values

# 5. Run the bot
python -m app.main
```

The bot will:
- Connect to MongoDB
- Query TMDB
- Post to your channel (if new releases found)
- Exit

---

## Testing manually

**Test the full pipeline:**
```bash
python -m app.main
```

**Widen the date window to find more results:**
```bash
LOOKBACK_DAYS=7 LOOKAHEAD_DAYS=7 python -m app.main
```

**Force an empty-update message:**
```bash
POST_EMPTY_UPDATE=true python -m app.main
```

**Test with a specific provider only:**
```bash
OTT_PROVIDERS=Netflix python -m app.main
```

**Check what's in MongoDB after a run:**
Use MongoDB Compass or the Atlas web UI, or:
```python
from pymongo import MongoClient
client = MongoClient("your-mongodb-uri")
for doc in client["ott_update_bot"]["posted_items"].find():
    print(doc)
```

---

## How duplicate prevention works

Every time the bot successfully posts a title to Telegram, it inserts a document into MongoDB:

```json
{
  "tmdb_id": 12345,
  "media_type": "movie",
  "provider": "Netflix",
  "title": "Some Movie",
  "release_date": "2026-09-10",
  "posted_at": "2026-09-10T03:00:00+00:00"
}
```

A **unique compound index** on `(tmdb_id, media_type, provider)` enforces that the same title on the same platform can never be inserted twice.

Before posting anything to Telegram, the bot checks MongoDB. If the record exists, the title is skipped. The bot is therefore **idempotent** — safe to run multiple times on the same day.

> **Important:** A record is only written to MongoDB **after** a successful Telegram post. If Telegram fails, the item stays unrecorded and will be retried on the next run.

---

## Deploying on Railway

### 1. Create a Railway project

1. Go to <https://railway.app/> and sign in.
2. Click **New Project → Deploy from GitHub repo**.
3. Select your repository.
4. Railway will detect the `Dockerfile` automatically.

### 2. Add environment variables

In your Railway service:
1. Go to **Service → Variables**.
2. Add all the variables from your `.env` file.

Never use a `.env` file in production — set variables directly in Railway.

### 3. Create a Cron Job service

Railway has a dedicated **Cron Job** service type:

1. In your project, click **+ New** → **Cron Job**.
2. Set the **Command**: `python -m app.main`
3. Set the **Schedule** (cron expression in UTC — see next section).
4. Add the same environment variables to this service.

> Alternatively, configure `railway.toml` with `startCommand = "python -m app.main"` and set `restartPolicyType = "NEVER"`.

---

## Configuring the Railway Cron Job

Railway cron expressions use **UTC time**.

India Standard Time (IST) is **UTC+5:30**, so subtract 5 hours 30 minutes from your desired IST time.

### IST → UTC conversion table

| Desired IST Time | UTC equivalent | Cron expression (UTC) |
|---|---|---|
| 06:00 IST | 00:30 UTC | `30 0 * * *` |
| 07:00 IST | 01:30 UTC | `30 1 * * *` |
| 07:30 IST | 02:00 UTC | `0 2 * * *` |
| **08:30 IST** | **03:00 UTC** | **`0 3 * * *`** ✅ recommended |
| 09:00 IST | 03:30 UTC | `30 3 * * *` |
| 10:00 IST | 04:30 UTC | `30 4 * * *` |
| 12:00 IST | 06:30 UTC | `30 6 * * *` |

**Recommended**: `0 3 * * *` (runs at 03:00 UTC = 08:30 IST every day).

The bot reads today's date from the `Asia/Kolkata` timezone at runtime, so even if Railway fires slightly early or late, the correct IST date is used.

---

## Project structure

```
telegram-ott/
│
├── app/
│   ├── __init__.py       — Package marker
│   ├── main.py           — Orchestrator (entry point)
│   ├── config.py         — Env-var loading + validation
│   ├── tmdb.py           — TMDB API client
│   ├── providers.py      — Provider alias map + normalization
│   ├── releases.py       — Source ABC + TMDBSource
│   ├── mongodb.py        — MongoDB client
│   ├── telegram_bot.py   — Telegram message sender
│   └── formatter.py      — Telegram message formatter
│
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile
├── railway.toml
└── README.md
```

### Adding a new source (future)

The `Source` abstract base class in `releases.py` is designed for extension:

```python
# Current
source = TMDBSource(config)
all_releases = source.fetch_releases(window)

# Future — add another source without changing main.py
sources = [TMDBSource(config), RSSSource(config)]
all_releases = []
for source in sources:
    all_releases.extend(source.fetch_releases(window))
```

---

## Adding a new OTT provider

1. Open [`app/providers.py`](app/providers.py).
2. Add the provider to `PROVIDER_ALIASES`:
   ```python
   "Hoichoi": [
       "Hoichoi",
   ],
   ```
3. Add an emoji to `PROVIDER_EMOJIS`:
   ```python
   "Hoichoi": "🎪",
   ```
4. Add the canonical name to `OTT_PROVIDERS` in your `.env`.

---

## TMDB Attribution

> This product uses the TMDB API but is not endorsed or certified by TMDB.

As required by TMDB's [terms of use](https://www.themoviedb.org/documentation/api/terms-of-use):
- Display the TMDB logo or attribution text when using their data.
- The formatter automatically adds "Data source: TMDB" to every post.
- Do not sell or redistribute raw TMDB data.

---

## Known Limitations

### TMDB watch-provider data does not include "date added to platform"

TMDB's watch-provider data reflects the **current** streaming availability of a title, not the exact date it was added to a platform. A title that arrived on Netflix six months ago will still appear in TMDB's provider data — but it will NOT appear in the bot's output because the bot filters by `primary_release_date` (theatrical/VOD release date), not by the platform-addition date.

**What this means in practice:**
- Titles are surfaced based on their TMDB release/air date, which is an approximation of "when it became available."
- A title released theatrically months ago but newly available on OTT will be missed unless its TMDB release date falls in the date window.
- Some titles may appear multiple times if LOOKBACK_DAYS is large. MongoDB prevents duplicate posts.

### TMDB's India streaming data may be incomplete

TMDB relies on community contributions for watch-provider data. Some titles, especially smaller regional films, may not have provider data for India even if they are available on a platform. Coverage improves over time.

### Provider name mapping

TMDB provider names can change. If a provider appears with a new name variant, add it to `PROVIDER_ALIASES` in `app/providers.py`.

### Rate limits

TMDB's API has rate limits (~40 requests/second). The bot handles 429 responses with automatic backoff. For very large provider lists, the bot may take a few minutes to run.

### Telegram message length

Telegram limits messages to 4096 characters. If more than ~60 titles are found, the message will be truncated. Consider narrowing `OTT_PROVIDERS` or reducing `LOOKBACK_DAYS` if this occurs.
