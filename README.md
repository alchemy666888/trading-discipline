# BTC Discipline Bot

BTC Discipline Bot is a single-user, self-hosted Telegram bot for pre-trade discipline on `BTCUSDT`. It is not a trading bot. It never places orders, connects to private exchange APIs, generates trade ideas, or runs AI behavior in v1. Its job is to force a complete pre-trade plan, monitor the user's invalidation level, escalate when invalidation is breached, and track adherence statistics.

## v1 Scope

- Telegram-only interface
- Single whitelisted chat ID
- Redis as the only authoritative datastore
- Binance BTCUSDT perpetual websocket monitoring in v1
- Bybit adapter reserved as a stub seam
- Deterministic sizing and leverage rules
- Weekly summary and daily healthy heartbeat
- Empty intelligence extension seam only

## Repository Layout

```text
src/
  app.py
  bot/
  db/
  events/
  exchange/
  intelligence/
  models/
  monitor/
  rules/
  stats/
tests/
  unit/
  integration/
  e2e/
```

## Requirements

- Python 3.11+
- Docker + Docker Compose
- Telegram bot token
- One allowed Telegram chat ID
- Network access from the bot container to Telegram, Redis, and Binance websocket endpoints

## Quick Start

1. Copy the example config and lock it down:

```bash
cp .env.example .env
chmod 0600 .env
```

2. Fill in at least:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TIMEZONE`

3. Start the stack:

```bash
docker compose up -d --build
```

4. Check health from Telegram:

```text
/health
```

5. Open a commitment flow:

```text
/new
```

## Configuration

The bot reads settings from environment variables and `.env`.

Required:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Important defaults:

- `EXCHANGE=binance`
- `SYMBOL=BTCUSDT`
- `LEVERAGE_BLOCK_THRESHOLD=20`
- `CONSECUTIVE_LOSS_THRESHOLD=2`
- `SIZE_REDUCTION_FACTOR=0.5`
- `FORM_TIMEOUT_SECONDS=600`
- `TIMEZONE=UTC`
- `REDIS_URL=redis://redis:6379/0`
- `REDIS_DATA_DIR=./data/redis`
- `REDIS_APPENDONLY=yes`

Health and alert cadence:

- `ALERT_INTERVAL_FIRST_WINDOW_SECONDS`
- `ALERT_INTERVAL_FIRST_WINDOW_DURATION_SECONDS`
- `ALERT_INTERVAL_AFTER_SECONDS`
- `MONITOR_DOWN_ALERT_DELAY_WITH_OPEN_TRADES_SECONDS`
- `MONITOR_DOWN_ALERT_DELAY_NO_OPEN_TRADES_SECONDS`
- `MONITOR_DOWN_REPEAT_WITH_OPEN_TRADES_SECONDS`
- `MONITOR_DOWN_REPEAT_NO_OPEN_TRADES_SECONDS`
- `HEARTBEAT_TIME_LOCAL`
- `HEARTBEAT_FILE_PATH`

`HEARTBEAT_FILE_PATH` is optional. When set to `/heartbeat/monitor.txt` in Docker Compose, the bot updates that file so an external monitor can alert if the process dies silently.

## Common Commands

- `/new` starts the 8-field pre-trade form
- `/closed <price>` closes the most recent open trade
- `/closed <id> <price>` closes a specific open trade
- `/justify <trade_id> <reason>` resolves a breach and resumes monitoring
- `/cancel` clears the in-progress form and returns to IDLE
- `/open` lists current `OPEN` and `OPEN_OVERRIDE` trades
- `/streak` shows the current loss streak and active size cap
- `/stats [days]` shows rolling stats
- `/setpnl <trade_id> <pnl>` overrides realized P&L for a closed trade
- `/health` shows websocket and Redis health
- `/signals` returns the v1 stub response only
- `/help [cmd]` shows command help

## Local Development

Install dependencies:

```bash
python -m pip install -e .
```

Run the bot directly:

```bash
python -m src.app
```

Run checks:

```bash
ruff check .
black --check .
mypy --strict src/
pytest
```

## Docker Runtime

`docker-compose.yml` is the primary runtime. It provides:

- `bot` built from the local `Dockerfile`
- `redis` from the official Redis image
- AOF persistence enabled with `/data` mounted from `${REDIS_DATA_DIR:-./data/redis}`
- `restart: always` on both services
- No public Redis port by default

Data survives `docker compose down` / `up -d` as long as the host-mounted Redis data directory remains intact.

## Health Model

`/health` reports:

- websocket connection status
- last tick age
- open trade count
- last websocket error
- Redis connectivity
- Redis AOF and persistence status

Daily heartbeat messages are sent only while the monitor is healthy. If the process restarts mid-breach, unresolved breach escalation is re-armed from level 0 and documented in [RUNBOOK.md](/Users/antee/Documents/projects/trading-discipline/RUNBOOK.md).

## v2 Extension Guide

The v1 intelligence seam is intentionally empty. Future read-only extensions must follow `REQ-010` and the boundary described in `design.md` Section 13.

- `src/intelligence/` may publish events and write only to the `signals` namespace in v2.
- It must never write trades, breaches, alerts, or conversation state.
- It must never influence whether a trade is opened, blocked, sized, justified, or closed.

The convention test in [tests/unit/test_intelligence_boundary.py](/Users/antee/Documents/projects/trading-discipline/tests/unit/test_intelligence_boundary.py) enforces this boundary.

## 24-Hour Smoke Test

Before live use, run a paper-trade smoke test for 24 hours:

1. Start the stack with Binance websocket access enabled.
2. Open one low-stakes paper trade with `/new`.
3. Watch `/health`, heartbeat delivery, and reconnect logs for a full day.
4. Confirm no false breaches, sane stats output, and recovery messages after any network blips.

See [RUNBOOK.md](/Users/antee/Documents/projects/trading-discipline/RUNBOOK.md) for the operational checklist.
