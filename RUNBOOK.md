# RUNBOOK

## Normal Operation

- Keep the stack running with `docker compose up -d`.
- Use `/health` after startup and after any alert storm.
- Expect a daily heartbeat message at `HEARTBEAT_TIME_LOCAL` only while the monitor is healthy.
- Expect weekly summary delivery every Monday at 09:00 in the configured timezone.

## If the Daily Heartbeat Stops Arriving

1. Run `docker compose ps` and confirm both `bot` and `redis` are up.
2. Run `docker compose logs --tail=200 bot` and look for websocket, Redis, or Telegram send errors.
3. Send `/health` in Telegram if the bot still responds.
4. If `HEARTBEAT_FILE_PATH` is enabled, check the file timestamp inside the mounted heartbeat directory.
5. If the bot process is down, restart with `docker compose up -d`.
6. If the process does not stay up, inspect Redis persistence and token configuration before restarting again.

## WS Down Alert Handling

If you receive a monitor-down alert:

1. Open `/health` and inspect websocket status and last tick age.
2. If open trades exist, prioritize whether you need to manage them manually on your exchange UI.
3. Watch for the recovery message. A recovery alert includes the coverage gap in seconds.
4. If the gap exceeded 60 seconds, treat the first post-reconnect evaluation as authoritative and review any new breach alerts immediately.

## Redis Unavailable

Symptoms:

- bot fails on startup
- `/health` shows Redis disconnected
- handlers reply with the generic internal error message

Actions:

1. Check `docker compose ps`.
2. Check `docker compose logs --tail=200 redis`.
3. Verify the host-mounted Redis data directory exists and is writable.
4. Restart Redis with `docker compose restart redis`.
5. Restart the bot after Redis is healthy again.

## Redis Persistence Failure

Symptoms:

- `/health` shows AOF disabled
- `/health` shows persistence directory not writable
- startup fails with a Redis persistence error

Actions:

1. Confirm `REDIS_APPENDONLY=yes`.
2. Confirm `${REDIS_DATA_DIR}` exists on the host.
3. Confirm the Docker daemon can write to that directory.
4. Restart the stack after correcting the mount or permissions.

## Restart Procedure

Graceful restart:

```bash
docker compose restart bot
```

Full stack restart:

```bash
docker compose down
docker compose up -d
```

Behavior notes:

- open trades are reloaded on startup
- unresolved breaches are re-armed on startup
- breach escalation restarts from level 0 after restart in v1

## Restore from Redis Backup

The Redis AOF/RDB files live inside the mounted `${REDIS_DATA_DIR}` host directory.

Restore steps:

1. Stop the stack with `docker compose down`.
2. Replace the contents of `${REDIS_DATA_DIR}` with the backup copy.
3. Start the stack with `docker compose up -d`.
4. Verify `/health`, `/open`, and `/stats`.

Prefer restoring the full directory contents together so Redis persistence metadata stays consistent.

## Rotate Telegram Bot Token

1. Create a new token with BotFather.
2. Update `TELEGRAM_BOT_TOKEN` in `.env`.
3. Restart the bot service:

```bash
docker compose restart bot
```

4. Verify `/health` and `/help`.

## Process-Level Failure Mitigations

This bot cannot self-notify if the entire process is dead. Use layered supervision:

- `docker-compose.yml` sets `restart: always` for both services
- [deploy/btc-discipline.service](/Users/antee/Documents/projects/trading-discipline/deploy/btc-discipline.service) is an optional `systemd` unit that supervises the Compose stack with `Restart=always`
- `HEARTBEAT_FILE_PATH` can expose a heartbeat timestamp to an external uptime monitor
- if heartbeat messages stop and the heartbeat file also stops updating, treat the stack as unhealthy even if Telegram is silent

## 24-Hour Real-Feed Smoke Test

Use one paper trade only.

1. Bring the stack up and confirm `/health` is green.
2. Open one trade with `/new`.
3. Leave the stack running for 24 hours.
4. Check for:
   - reconnect logs matching real network blips
   - no false breach alerts
   - healthy daily heartbeat behavior
   - sane `/stats`, `/streak`, and `/open` output
5. If you see any monitor-down alerts, verify a matching recovery message appears.

Do not treat production use as complete until this smoke test has passed in your environment.
