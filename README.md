# Auto Telegram News

🇬🇧 English | [🇷🇺 Русский](README.ru.md)

[![Tests](https://github.com/ispy4you/auto-telegram-news/actions/workflows/tests.yml/badge.svg)](https://github.com/ispy4you/auto-telegram-news/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](requirements.txt)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Release](https://img.shields.io/github/v/release/ispy4you/auto-telegram-news?include_prereleases)](https://github.com/ispy4you/auto-telegram-news/releases)

A self-hosted bot that monitors public Telegram channels, turns their posts into ready-to-publish
news with AI, and publishes them to your own channels via the Telegram Bot API — with a web admin
panel to review, edit, and approve everything in between.

Built as a production-ready MVP, not a toy: real-time ingestion, multi-layer deduplication,
per-channel publish schedules, multi-project support, and a hardened admin UI.

---

## Contents

- [Architecture](#architecture)
- [Features](#features)
- [Stack](#stack)
- [Getting started](#getting-started)
- [Configuration](#configuration-env)
- [Running the app](#running-the-app)
- [Using the admin panel](#using-the-admin-panel)
- [Database migrations](#database-migrations)
- [Limitations](#limitations)
- [Testing](#testing)
- [Releases](#releases)
- [Contributing](#contributing)
- [License](#license)

---

## Architecture

```mermaid
flowchart TD
    subgraph SRC["📡 Sources (Telegram)"]
        S1[Channel 1]
        S2[Channel 2]
        SN[Channel N ...]
    end

    subgraph COLLECT["🔄 Content collection"]
        EL["⚡ Event listener\nTelethon · real-time\nNewMessage handler"]
        POLL["🕐 Scheduler\nAPScheduler · fallback\non lost connection"]
    end

    subgraph DB["🗄️ PostgreSQL"]
        RAW[(raw_posts)]
        MEDIA[(media_items)]
        LOGS[(action_logs)]
    end

    subgraph PIPE["⚙️ Processing pipeline"]
        DEDUP["🔍 Deduplication\nSHA-256 → rapidfuzz → fastembed\nsemantic comparison"]
        AI["🤖 AI generation\nTimeweb AI Gateway\nOpenAI-compatible API"]
    end

    subgraph PUB["📤 Publishing"]
        BOT["🤖 Telegram bot\naiogram 3.x"]
        SCHED_PUB["🗓️ Schedule\nper-channel\ntime windows"]
    end

    subgraph TGT["📢 Target channels"]
        T1[Channel A]
        T2[Channel B]
    end

    ADMIN["🖥️ Admin UI\nFastAPI · Jinja2 · Bootstrap 5\nlocalhost:8000"]

    S1 & S2 & SN -->|"NewMessage event"| EL
    S1 & S2 & SN -->|"polling fallback"| POLL
    EL -->|"save post + media"| RAW
    POLL -->|"save post + media"| RAW
    EL & POLL --> MEDIA

    RAW --> DEDUP
    DEDUP -->|"status READY"| AI
    AI -->|"draft"| BOT
    BOT --> SCHED_PUB
    SCHED_PUB --> T1 & T2

    ADMIN <-->|"manage, review, publish"| DB
    ADMIN -->|"manual publish"| BOT
    DB --> LOGS
```

---

## Features

### Content collection
- Reads posts from any number of public Telegram channels via a **Telethon user session** —
  no need to be an admin of the source channel.
- **Real-time mode**: a persistent Telethon connection with a `NewMessage` handler — new posts are
  saved instantly, without waiting for the next scheduler tick.
- **Automatic catch-up**: on startup (or reconnect), missed messages per channel are backfilled.
- **Polling fallback**: the scheduler keeps working if the connection drops; the fetch step is
  skipped automatically while the event listener is active.
- Automatic reconnection on disconnect (30s pause, then retry).
- Incremental collection: remembers the last-read message per channel, never re-fetches the same
  post twice.
- Configurable background scheduler interval (30s – 24h, default 120s).
- Downloads and stores attachments: photos, videos, documents; correctly handles albums (grouped
  messages).

### Deduplication
- **SHA-256** — instant exact-duplicate detection.
- **rapidfuzz** — fuzzy duplicate matching with a configurable threshold (50–100%, default 88%).
- **fastembed** (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`) — semantic
  deduplication that catches meaning-level duplicates fuzzy matching misses (e.g. two differently
  worded posts about the same event). Threshold and enable/disable toggle live in the admin UI;
  the model is ~220MB and loads once.
- Text normalization before comparison: strips links, punctuation, extra whitespace.
- 48-hour comparison window; embeddings are cached in the database for reuse.

### AI generation
- Integrates with **Timeweb AI Gateway** (OpenAI-compatible endpoint) — pluggable to any
  OpenAI-compatible API.
- System prompt and user template are stored in the database and editable directly from the admin
  UI.
- Configurable temperature, token limit, timeout.
- Automatic post scoring: `SUITABLE` / `REJECTED`.
- Graceful handling of truncated JSON responses from the model.

### Post management
- State machine: `NEW → READY → GENERATED → PUBLISHED`.
- Bulk actions: generate, reject, or delete multiple posts at once.
- Manual editing of AI-generated text before publishing.
- Publish the original text without AI processing.
- Re-generate already-processed posts.
- **Telegram-style preview** right on the post page: message bubble, media grid, character
  counter, `Ctrl+P` hotkey — updates live as you edit.

### Publishing
- Sends to multiple target channels via **aiogram 3.x**.
- Smart media handling:
  - a single file is sent with a caption;
  - multiple files become a media group, with the text on the first item;
  - long text is sent as a separate message after the media.
- Per-publish toggle for "send with media / text only".
- Routes: map which source feeds which target channel; with no routes configured, posts fan out to
  all active channels.
- Auto-publish: a fully hands-off pipeline with no operator step.
- Publish job tracking with retries (up to 3).
- **Publish schedule**: each target channel gets a time window (`publish_from` / `publish_to`).
  Posts outside the window are queued and published automatically once the next window opens.
- `telegram_message_id` is stored after sending, for future edits and analytics.

### Multi-project support
- Unlimited **projects** — isolated spaces with their own sources, channels, and routes.
- Fast switching via a header dropdown; all counters and lists are filtered by the current project.
- Full CRUD for projects: create, rename, delete (with accidental-deletion protection).
- All pre-existing data is migrated into a "Default" project automatically on first run.

### Operator notifications
- The bot sends **Telegram messages** to the operator when unprocessed drafts pile up past a
  threshold.
- Optional pipeline-error notifications.
- A test-message button right in Settings.
- Anti-spam: re-notifies only when the draft count has grown since the last notice.

### Web UI & dashboard
- Overview: active sources, new posts, duplicates, drafts, published today/this week.
- Scheduler status: interval, next run, a "run now" button.
- Recent action log on the home page.

### Publishing statistics
- Funnel: collected → ready → generated → published.
- Daily publish chart for the last 30 days (**Chart.js**).
- Top sources by publish count.
- Per-target-channel breakdown with progress bars.

### Logging & audit
- Full history of user actions and system events.
- Filterable by event type and text.
- Paginated (100 records per page).

### Security
- Session-based auth with constant-time HMAC comparison.
- CSRF protection on all forms: double-submit token + session storage.
- Media files are served through an authorized `/media/` route, not as public static files.
- In production mode, startup is blocked unless default secrets have been changed.

---

## Stack

| Layer | Libraries |
|---|---|
| Web | FastAPI, Uvicorn, Jinja2, Bootstrap 5 |
| Database | PostgreSQL + psycopg2-binary, SQLAlchemy 2.x (SQLite for local dev only) |
| Telegram (reading) | Telethon |
| Telegram (publishing) | aiogram 3.x |
| AI | httpx + OpenAI-compatible endpoint |
| Deduplication | rapidfuzz + fastembed (paraphrase-multilingual-MiniLM-L12-v2) |
| Scheduler | APScheduler |
| Security | itsdangerous (CSRF) |
| Charts | Chart.js |
| Proxy | PySocks (SOCKS5 / HTTP / MTProxy) |

---

## Getting started

### Quick start with Docker (recommended)

The fastest way to try the app — no Python/PostgreSQL install needed, just Docker.

```bash
git clone https://github.com/ispy4you/auto-telegram-news.git
cd auto-telegram-news
cp .env.example .env
# edit .env: set APP_SECRET_KEY, ADMIN_PASSWORD, and (optionally, to enable collection/AI/
# publishing right away) TELEGRAM_API_ID/HASH, TELEGRAM_BOT_TOKEN, TIMEWEB_AI_GATEWAY_*

docker compose up -d --build
```

This starts both the app and a PostgreSQL database (in a second container); tables are created
automatically. Open **http://localhost:8000/login**, then go to **Settings → Telegram account**
and log in with QR code or phone + code — right in the browser, no terminal access needed. The
session is written to `./data/telegram_session/` on the host (bind-mounted into the container), so
it survives rebuilds and restarts.

By default the database uses `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` from `.env`
(falling back to `tgnews`/`tgnews`/`tgnews` if unset) — set a real password there for anything
beyond local testing, since `docker-compose.yml` builds `DATABASE_URL` from these automatically.

To stop: `docker compose down` (add `-v` to also wipe the database volume).

### Manual installation

Prefer running it directly with your own Python/PostgreSQL instead of Docker? See below.

#### Ubuntu / Debian

```bash
# 1. Python 3.12+ and system dependencies
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev git

# 2. PostgreSQL
sudo apt install -y postgresql postgresql-contrib libpq-dev

# 3. Clone the project
git clone https://github.com/ispy4you/auto-telegram-news.git
cd auto-telegram-news

# 4. Virtual environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 5. Config
cp .env.example .env
# edit .env
```

#### macOS (local development)

```bash
brew install postgresql@17
echo 'export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
brew services start postgresql@17

git clone https://github.com/ispy4you/auto-telegram-news.git
cd auto-telegram-news
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

#### Creating the PostgreSQL database

**Ubuntu:**
```bash
sudo -u postgres psql -c "CREATE USER tgnews WITH PASSWORD 'yourpassword';"
sudo -u postgres psql -c "CREATE DATABASE tgnews OWNER tgnews;"
```

**macOS (Homebrew, no-password local user):**
```bash
createdb tgnews
```

Set in `.env`:
```dotenv
# Ubuntu:
DATABASE_URL=postgresql://tgnews:yourpassword@localhost/tgnews
# macOS (Homebrew, your OS username):
# DATABASE_URL=postgresql://your_macos_username@localhost/tgnews
```

Tables are created automatically on first run (`Base.metadata.create_all`).

> SQLite (`sqlite:///./data/app.db`) is supported for local development without PostgreSQL, but
> is not recommended in production due to concurrent-write limitations.

### Getting TELEGRAM_API_ID and TELEGRAM_API_HASH

1. Go to [my.telegram.org](https://my.telegram.org).
2. Log in with your account.
3. Create an app under **API development tools**.
4. Copy `api_id` and `api_hash` into `.env`.

#### Creating a Telethon user session

Start the app, then go to **Settings → Telegram account** in the admin UI and log in with a QR
code or phone + code (2FA password too, if enabled) — right in the browser.

A CLI fallback also still works, useful for headless setups:

```bash
python -m app.cli.init_telegram_session
```

Both save the session to `TELEGRAM_SESSION_PATH`.

### Creating a bot via BotFather

1. Message `@BotFather`.
2. Run `/newbot`.
3. Copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.

### Adding the bot to a target channel

1. Open the channel's settings → **Administrators**.
2. Add the bot and grant it permission to post messages.
3. In the admin UI, click `Test` next to the target channel.

---

## Configuration (`.env`)

```dotenv
# App
APP_ENV=local                        # local | production
APP_HOST=127.0.0.1
APP_PORT=8000
APP_SECRET_KEY=change-me             # must be changed in production
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me             # must be changed in production
ADMIN_AUTH_ENABLED=true

# Database
DATABASE_URL=postgresql://tgnews:yourpassword@localhost/tgnews

# Telegram
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_SESSION_PATH=./data/telegram_session/user.session
TELEGRAM_BOT_TOKEN=

# Proxy (optional)
TELEGRAM_PROXY_TYPE=                 # socks5 | http | mtproxy | ""
TELEGRAM_PROXY_HOST=
TELEGRAM_PROXY_PORT=
TELEGRAM_PROXY_USERNAME=
TELEGRAM_PROXY_PASSWORD=
TELEGRAM_PROXY_SECRET=               # MTProxy only

# AI Gateway
TIMEWEB_AI_GATEWAY_API_KEY=
TIMEWEB_AI_GATEWAY_BASE_URL=
TIMEWEB_AI_GATEWAY_MODEL=
AI_TEMPERATURE=0.4
AI_MAX_TOKENS=1600
AI_TIMEOUT_SECONDS=60

# Pipeline
FETCH_INTERVAL_SECONDS=120
DEFAULT_LOOKBACK_LIMIT=50
MAX_MEDIA_MB=50
AUTO_PUBLISH_ENABLED=false
DEFAULT_POST_MODE=manual
```

---

## Running the app

> Using Docker? See [Quick start with Docker](#quick-start-with-docker-recommended) — `docker
> compose up -d` / `docker compose down` is all you need. The sections below are for the manual
> (non-Docker) install.

### Locally

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Or without activating the environment:

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Ubuntu / VPS (production via systemd)

```bash
# 1. System dependencies
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev git postgresql postgresql-contrib libpq-dev

# 2. Database
sudo -u postgres psql -c "CREATE USER tgnews WITH PASSWORD 'yourpassword';"
sudo -u postgres psql -c "CREATE DATABASE tgnews OWNER tgnews;"

# 3. Project
git clone https://github.com/ispy4you/auto-telegram-news.git /opt/tg-news-mvp
cd /opt/tg-news-mvp
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: DATABASE_URL, APP_SECRET_KEY, ADMIN_PASSWORD, Telegram keys

# 4. Telethon session (one-time, interactive)
python -m app.cli.init_telegram_session

# 5. systemd service
sudo tee /etc/systemd/system/tgnews.service > /dev/null <<EOF
[Unit]
Description=Telegram News Bot
After=network.target postgresql.service

[Service]
User=$USER
WorkingDirectory=/opt/tg-news-mvp
ExecStart=/opt/tg-news-mvp/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tgnews
sudo systemctl start tgnews
sudo systemctl status tgnews
```

### Managing services

```bash
# App
sudo systemctl start tgnews
sudo systemctl stop tgnews
sudo systemctl restart tgnews
sudo systemctl status tgnews
journalctl -u tgnews -f          # live logs

# PostgreSQL
sudo systemctl start postgresql
sudo systemctl stop postgresql
sudo systemctl status postgresql
```

Access the admin UI over an SSH tunnel (`ssh -L 8000:127.0.0.1:8000 user@server`) or put nginx in
front as a reverse proxy with HTTPS.

---

## Using the admin panel

1. **Projects** → create separate projects if needed; switch via the header menu.
2. **Sources** → add source channels by `@username` or `https://t.me/...`.
3. **Targets** → add target channels with their `chat_id`, verify with the `Test` button, set
   publish schedules.
4. **Routes** → configure which source feeds which target channel.
5. **Dashboard** → click "Run collection now" or wait for the automatic run.
6. **Posts** → review new posts, trigger AI generation, edit and publish. Use `Ctrl+P` for the
   Telegram-style preview.
7. **Stats** → view the funnel, daily publish chart, top sources.
8. **Settings** → connect your Telegram account (QR code or phone + code), tune deduplication
   thresholds, prompts, collection interval, operator notifications.

---

## Database migrations

Migrations run automatically on every startup via `ALTER TABLE … ADD COLUMN` inside a try/except.
Nothing needs to be run manually.

Columns added by auto-migration:
- `target_channels.publish_from` / `publish_to` — publish time window
- `generated_posts.telegram_message_id` — message ID after sending
- `source_channels.project_id` / `target_channels.project_id` — multi-project support
- `raw_posts.embedding` — vector embedding for semantic deduplication

All Telegram ID columns (`telegram_message_id`, `telegram_grouped_id`, `last_message_id`,
`telegram_channel_id`) are automatically widened to `BIGINT` — Telegram uses 64-bit numbers that
don't fit in a standard `INTEGER`.

---

## Limitations

- The Telegram Bot API caps the size of files sent through it.
- Media captions are limited to ~1024 characters; longer text is sent as a separate message.
- Sources are read via a user session — the bot account should not be an admin of the source.
- Public channels must be reachable by the user session's account.
- Enable auto-publish carefully: there's no additional review step before sending.
- Semantic deduplication needs ~220MB for the model and adds noticeable CPU load. On low-power
  VPS instances, it's recommended to keep it disabled.

---

## Testing

```bash
pytest
```

CI runs the full suite on every push and pull request to `main`.

---

## Releases

Versions and the changelog are generated automatically from
[Conventional Commits](https://www.conventionalcommits.org/) via
[release-please](https://github.com/googleapis/release-please) — see the
[Releases page](https://github.com/ispy4you/auto-telegram-news/releases) for the version history.

---

## Contributing

Contributions are very welcome — bug reports, feature ideas, docs fixes, and pull requests.

- Found a bug or have an idea? Open an [issue](../../issues/new/choose).
- Ready to send code? See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup and PR workflow.
- Found a security issue? Please follow [SECURITY.md](SECURITY.md) instead of opening a public
  issue.

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md) code of conduct.

Issues and PRs in Russian are just as welcome as in English.

---

## License

[MIT](LICENSE) © Ivan Chuzhmaroff
