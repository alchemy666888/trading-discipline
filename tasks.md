# Tasks: BTC Discipline Bot

## Phase 1: Preparation

- [ ] **TASK-001: Initialize project skeleton**
  - Description: Create the `btc-discipline-bot/` repo. Add `pyproject.toml` (Python 3.11+, deps: `python-telegram-bot>=20`, `websockets`, `redis>=5`, `pydantic>=2`, `structlog`, `apscheduler`, `pytest`, `pytest-asyncio`, `freezegun`, `ruff`, `black`, `mypy`). Add `.gitignore`, `.env.example`, empty `README.md`, `src/__init__.py`.
  - Requirements covered: NFR-maintainability
  - Files: `pyproject.toml`, `.gitignore`, `.env.example`, `README.md`, `src/__init__.py`
  - Acceptance: `pip install -e .` succeeds; `ruff`, `black --check`, `mypy --strict` all run cleanly on the empty skeleton.
  - Dependencies: none

- [ ] **TASK-002: Config module**
  - Description: Implement `src/config.py` as a Pydantic `Settings` class loading from env + `.env`. Fields per REQ-008 and REQ-011, including `REDIS_URL`, `REDIS_DATA_DIR`, and Redis persistence-related settings. Validate on construction.
  - Requirements: REQ-008, REQ-011
  - Files: `src/config.py`, `.env.example`, `tests/unit/test_config.py`
  - Acceptance: missing required env vars cause a clear startup error; valid env loads.
  - Dependencies: TASK-001

## Phase 2: Data model / Redis datastore

- [ ] **TASK-003: Pydantic models**
  - Description: Implement `src/models/trade.py`, `breach.py`, `alert.py`, `conversation.py`, `signal.py`, `events.py` matching the schema in design.md §3 and the event types in §13.1. Include enums for `direction`, `regime`, `status`, `user_response`, `severity`, `EventType`. Also implement `TradeDraft` (the in-progress form data) and `RuleContext` dataclass in `src/rules/context.py` per REQ-010 (`signals` field defaults to empty mapping).
  - Requirements: REQ-001, REQ-005, REQ-010
  - Files: `src/models/*`, `src/rules/context.py`, `tests/unit/test_models.py`
  - Acceptance: round-trip JSON serialization works; invalid values raise `ValidationError`; `RuleContext(trade_draft=..., recent_trades=[])` constructs with `signals == {}`.
  - Dependencies: TASK-001

- [ ] **TASK-004: Redis keyspace and migration contract**
  - Description: Create `src/db/keyspace.py` and `src/db/migrations.py` for the Redis datastore contract in design.md §3. Define typed key builders for trades, breaches, alerts, conversation state, signals, indexes, counters, and `schema:version`. Implement idempotent migration bootstrap that initializes the key-contract version without creating SQLite files.
  - Requirements: REQ-001, REQ-004, REQ-005, REQ-007, REQ-010, REQ-011
  - Files: `src/db/keyspace.py`, `src/db/migrations.py`, `tests/unit/test_keyspace.py`, `tests/integration/test_redis_migrations.py`
  - Acceptance: key builders never use raw user text; migration bootstrap sets `schema:version`; `signals:*` keys remain absent/empty after init; no `.db` file is created.
  - Dependencies: TASK-001

- [ ] **TASK-005: Redis repository layer**
  - Description: Implement `src/db/repo.py` with `redis.asyncio`. Methods: `create_trade`, `get_trade`, `list_open_trades`, `close_trade`, `mark_override`, `create_breach`, `get_open_breach`, `resolve_breach`, `record_alert`, `get_conversation_state`, `set_conversation_state`, `clear_conversation_state`, `recent_closed_trades(n)`, `consecutive_loss_count()`, `apply_migrations()`, plus signal accessors `list_active_signals()` and `insert_signal(...)` (the latter is callable only from `src/intelligence/`; see TASK-031 for the convention check). Use Redis `MULTI/EXEC` transactions or Lua scripts for multi-key state transitions.
  - Requirements: REQ-001, REQ-003, REQ-005, REQ-007, REQ-010, REQ-011, NFR-security
  - Files: `src/db/repo.py`, `src/db/scripts/*.lua`, `tests/integration/test_repo.py`
  - Acceptance: integration test against an isolated Redis test database or disposable Redis container passes the full lifecycle (open → breach → response → close); user text containing Redis/key-control characters is stored only as values and does not affect keys or commands; signal accessors work but are not exercised by v1 production code.
  - Dependencies: TASK-003, TASK-004

- [ ] **TASK-032: Redis connection health and persistence checks**
  - Description: Implement repository/startup checks that verify Redis is reachable, reports append-only persistence enabled, and has a writable persistence directory from the container perspective. Expose Redis connectivity and persistence status to `/health`.
  - Requirements: REQ-011, REQ-009
  - Files: `src/db/repo.py`, `src/monitor/health.py`, `src/bot/handlers.py`, `tests/integration/test_redis_health.py`
  - Acceptance: startup fails with a clear error when Redis is unavailable; `/health` includes Redis OK/failure and AOF enabled/disabled status; tests simulate Redis unavailable and Redis AOF disabled.
  - Dependencies: TASK-005

- [ ] **TASK-030: Event bus (REQ-010)**
  - Description: Implement `src/events/bus.py`. In-process asyncio pub/sub: `EventBus` class with `async publish(event)` and `subscribe(event_type, handler)`. Subscribers receive events via `asyncio.gather`; the bus catches and logs handler exceptions so a misbehaving subscriber cannot break a publisher. Define event payload dataclasses in `src/models/events.py` per design.md §13.1 (`tick`, `breach_detected`, `breach_resolved`, `trade_opened`, `trade_closed`, `monitor_down`, `monitor_recovered`). v1 has no production subscribers other than what already exists in the modules; future intelligence modules subscribe.
  - Requirements: REQ-010
  - Files: `src/events/bus.py`, `src/models/events.py`, `tests/unit/test_event_bus.py`
  - Acceptance: a test subscriber receives every event type in the order published; a deliberately-failing subscriber does not prevent other subscribers from receiving the event; publishing when there are no subscribers is a no-op.
  - Dependencies: TASK-003

## Phase 3: Rules engine

- [ ] **TASK-006: Leverage block rule**
  - Description: Implement `src/rules/leverage.py` exposing `check(ctx: RuleContext, threshold: int) -> LeverageDecision` (enum: `ALLOW`, `BLOCK_NEEDS_OVERRIDE`, `REJECT_OUT_OF_RANGE`). Pure function, no I/O. Per REQ-010, the function MUST NOT read from `ctx.signals` in v1; assert in tests that decisions are identical whether `ctx.signals` is empty or populated.
  - Requirements: REQ-002, REQ-010
  - Files: `src/rules/leverage.py`, `tests/unit/test_rules_leverage.py`
  - Acceptance: tests cover 1, threshold−1, threshold, threshold+1, 125, 126, 0, −1; one test feeds a non-empty `ctx.signals` and verifies the decision is unchanged.
  - Dependencies: TASK-003

- [ ] **TASK-007: Sizing rule**
  - Description: Implement `src/rules/sizing.py` exposing `compute_size_cap(ctx: RuleContext, threshold: int, factor: float) -> Optional[float]`. Returns `None` if no cap is active. Implements REQ-003 exactly, including the no-winners and breakeven cases. Per REQ-010, MUST NOT read from `ctx.signals` in v1.
  - Requirements: REQ-003, REQ-010
  - Files: `src/rules/sizing.py`, `tests/unit/test_rules_sizing.py`
  - Acceptance: tests cover: no history, 1 loss, 2 losses, 3 losses + winner reset, breakeven between losses, no winners ever, mixed history; one test feeds a non-empty `ctx.signals` and verifies the cap is unchanged.
  - Dependencies: TASK-003

- [ ] **TASK-008: Field validators**
  - Description: Implement `src/rules/validation.py` for invalidation-vs-direction, thesis length, regime enum, size > 0, leverage range, max_loss > 0. Validators may take individual fields directly; the rule-level orchestrator (called from forms) takes `RuleContext`.
  - Requirements: REQ-001, REQ-010
  - Files: `src/rules/validation.py`, `tests/unit/test_rules_validation.py`
  - Acceptance: 100% branch coverage on validators; clear error messages naming the field and rule.
  - Dependencies: TASK-003

## Phase 4: Exchange adapter

- [ ] **TASK-009: ExchangeAdapter ABC**
  - Description: Define `src/exchange/base.py` with the `ExchangeAdapter` ABC: `async def stream_ticks() -> AsyncIterator[Tick]`, `async def healthy() -> bool`, `async def close()`. Define `Tick` dataclass with `price: float`, `ts: datetime`.
  - Requirements: REQ-004
  - Files: `src/exchange/base.py`
  - Acceptance: subclassing without implementing methods raises `TypeError`.
  - Dependencies: TASK-001

- [ ] **TASK-010: Binance adapter**
  - Description: Implement `src/exchange/binance.py`. Subscribe to `wss://fstream.binance.com/ws/btcusdt@markPrice@1s`. Yield normalized `Tick`. Reconnect with exponential backoff (1,2,4,8,16,32,60s, then steady 60s). Drop stale ticks (> 30s old). Emit connection-state events (`CONNECTED`, `DISCONNECTED`, `STALE`, `RECONNECTED` with gap duration) on an internal pub/sub so the monitor-health subsystem (TASK-029) can apply REQ-009 tiered alerting.
  - Requirements: REQ-004
  - Files: `src/exchange/binance.py`, `tests/unit/test_exchange_binance.py`
  - Acceptance: mocked-websocket tests simulate disconnect, stale tick, and reconnect, and verify the correct events are emitted with correct timing; one integration test against the real endpoint, marked `@pytest.mark.network` (off by default).
  - Dependencies: TASK-009

## Phase 5: Monitor + alerts

- [ ] **TASK-011: Breach evaluator**
  - Description: Implement `src/monitor/breach.py` as a pure function: `is_breach(direction: Direction, invalidation: float, tick_price: float) -> bool`. Long: tick ≤ invalidation. Short: tick ≥ invalidation.
  - Requirements: REQ-004
  - Files: `src/monitor/breach.py`, `tests/unit/test_breach.py`
  - Acceptance: tests for long/short × above/below/equal to invalidation.
  - Dependencies: TASK-003

- [ ] **TASK-012: Alert dispatcher with escalation**
  - Description: Implement `src/monitor/alerts.py`. On a new breach, send initial alert and schedule escalation: every 60s for the first 5 minutes, every 300s after. De-dup: while a breach is unresolved, additional ticks do not create new alert sequences. On breach resolution (`/closed` or `/justify`), cancel scheduled re-sends.
  - Requirements: REQ-005
  - Files: `src/monitor/alerts.py`, `tests/unit/test_alerts.py`
  - Acceptance: time-mocked test (`freezegun`) verifies cadence and dedup. Persistent send failure does not crash the loop.
  - Dependencies: TASK-005

- [ ] **TASK-029: Monitor health subsystem (REQ-009)**
  - Description: Implement `src/monitor/health.py`. Subscribe to exchange-adapter connection-state events. Maintain rolling state: `last_tick_at`, `currently_down_since`, recent-disconnect history (for flapping detection). When down ≥ `MONITOR_DOWN_ALERT_DELAY_WITH_OPEN_TRADES_SECONDS` AND open trades exist, send Telegram alert; repeat every `MONITOR_DOWN_REPEAT_WITH_OPEN_TRADES_SECONDS`. Use the longer no-open-trade thresholds when no positions are open. On reconnect, send recovery message (with coverage-gap warning if gap > 60s) and trigger re-evaluation of all open trades against the next tick. Schedule daily heartbeat via APScheduler at `HEARTBEAT_TIME_LOCAL`; suppress heartbeat if currently in a down state. Detect flapping (≥ 5 disconnects in 10 minutes) and skip the debounce delay on the next disconnect. Optionally write a heartbeat file each minute (path configurable) for an external uptime monitor.
  - Requirements: REQ-009
  - Files: `src/monitor/health.py`, `tests/unit/test_monitor_health.py`, `tests/integration/test_monitor_health.py`
  - Acceptance: with `freezegun`, simulate (a) 15s disconnect + open trade → exactly one alert and one recovery, (b) 8s disconnect + open trade → no alert, (c) 70s disconnect, no open trades → one alert at ~60s, (d) flapping pattern triggers immediate alert on the 6th disconnect, (e) daily heartbeat fires at configured local time when healthy and is suppressed when unhealthy, (f) coverage-gap > 60s on reconnect triggers re-evaluation of open trades.
  - Dependencies: TASK-005, TASK-010, TASK-012

## Phase 5 (continued)

- [ ] **TASK-013: Monitor loop**
  - Description: Implement `src/monitor/monitor.py`. On startup, load all `OPEN`/`OPEN_OVERRIDE` trades. Consume ticks from the adapter; for each tick, evaluate each open trade. On breach, atomically create the breach record (with a status guard) and call the alert dispatcher. Handle reconnects and stale streams. Forward connection-state events from the adapter to the monitor-health subsystem (TASK-029). On post-gap reconnect signaled by health, re-evaluate all open trades on the first new tick. Publish `tick`, `breach_detected` events on the event bus (TASK-030); also forward `monitor_down`, `monitor_recovered` events emitted by TASK-029.
  - Requirements: REQ-004, REQ-005, REQ-009, REQ-010, NFR-reliability
  - Files: `src/monitor/monitor.py`, `tests/integration/test_monitor.py`
  - Acceptance: integration test with a fake adapter + in-memory DB verifies a scripted tick stream produces correct breach records and no duplicates; gap-through scenario produces a breach on first post-reconnect tick; a test subscriber on the event bus receives the expected `tick` and `breach_detected` events.
  - Dependencies: TASK-010, TASK-011, TASK-012, TASK-029, TASK-030, TASK-005

## Phase 6: Bot

- [ ] **TASK-014: Form state machine**
  - Description: Implement `src/bot/forms.py`. Conversational state machine for `/new` per requirements.md §7 and design.md §5. Persists partial state to `conversation_state` table on every transition. 10-minute idle timeout reaper.
  - Requirements: REQ-001, REQ-002, REQ-003, BR-1
  - Files: `src/bot/forms.py`, `tests/integration/test_forms.py`
  - Acceptance: scripted conversations produce the expected DB state; timeout produces no trade.
  - Dependencies: TASK-005, TASK-006, TASK-007, TASK-008

- [ ] **TASK-015: Whitelist decorator**
  - Description: Implement `src/bot/whitelist.py`. `@whitelisted` decorator reads allowed chat ID from config, rejects others silently with a WARN log.
  - Requirements: NFR-security, BR-4
  - Files: `src/bot/whitelist.py`, `tests/unit/test_whitelist.py`
  - Acceptance: messages from other chat IDs produce no reply and a WARN log entry.
  - Dependencies: TASK-002

- [ ] **TASK-016: Command handlers**
  - Description: Implement `src/bot/handlers.py` with handlers for `/new`, `/closed`, `/justify`, `/cancel`, `/open`, `/streak`, `/stats`, `/setpnl`, `/health`, `/signals`, `/help`. Every handler is `@whitelisted`. `/closed` and `/justify` interact with the alert dispatcher to resolve breaches. Trade-lifecycle handlers (`/new` completion, `/closed`) publish `trade_opened`, `trade_closed`, `breach_resolved` events on the event bus (TASK-030). `/signals` is implemented as a stub per REQ-010 — replies: `"Intelligence layer not configured. v2 feature — see REQ-010."`
  - Requirements: REQ-002, REQ-003, REQ-005, REQ-006, REQ-007, REQ-010, NFR-security
  - Files: `src/bot/handlers.py`, `tests/integration/test_handlers.py`
  - Acceptance: mocked Telegram tests for each command's happy path and one failure path; `/signals` returns the documented stub string; a test subscriber on the bus receives `trade_opened` and `trade_closed` events at the right times.
  - Dependencies: TASK-014, TASK-015, TASK-030

- [ ] **TASK-017: Message formatting**
  - Description: Implement `src/bot/formatting.py` with every user-facing string (prompts, alerts, errors, confirmations) as a named function or template. Centralizing copy makes future tone adjustments one-place changes.
  - Requirements: REQ-001 (prompts), REQ-005 (alerts)
  - Files: `src/bot/formatting.py`
  - Acceptance: every prompt and alert in requirements.md §7 and design.md §5 has a corresponding function.
  - Dependencies: TASK-001

## Phase 7: Stats + reporting

- [ ] **TASK-018: Stats calculator**
  - Description: Implement `src/stats/calculator.py` with `compute_stats(trades, breaches, window_days)` returning the metrics in REQ-007. Pure function over already-loaded data.
  - Requirements: REQ-007
  - Files: `src/stats/calculator.py`, `tests/unit/test_stats.py`
  - Acceptance: deterministic on a fixture; per-regime breakdown correct; adherence rate uses correct denominator (resolved breaches only).
  - Dependencies: TASK-005

- [ ] **TASK-019: Weekly report scheduler**
  - Description: APScheduler job at Mon 09:00 in the configured TZ. Runs the stats calculator over the last 7 days and pushes a Telegram message.
  - Requirements: REQ-007
  - Files: `src/stats/report.py`, `tests/integration/test_report.py`
  - Acceptance: scheduled job fires at the correct local time in a frozen-clock test.
  - Dependencies: TASK-016, TASK-018

## Phase 8: Integration

- [ ] **TASK-020: Application entrypoint**
  - Description: Implement `src/app.py`. Loads config, connects to Redis, applies Redis datastore migrations, verifies Redis persistence health (TASK-032), constructs the event bus (TASK-030), exchange adapter, monitor, alert dispatcher, monitor-health subsystem, bot handlers, scheduler. Passes the event bus instance into every component that publishes or subscribes. Starts both coroutines under `asyncio.gather()` with structured logging.
  - Requirements: all
  - Files: `src/app.py`, `tests/integration/test_app_startup.py`
  - Acceptance: process starts, Redis key contract initialized, `/health` returns OK including Redis status, event bus is reachable from at least one test subscriber registered at startup.
  - Dependencies: TASK-013, TASK-016, TASK-019, TASK-030, TASK-032

- [ ] **TASK-021: Restart resilience**
  - Description: On startup, the monitor picks up all `OPEN`/`OPEN_OVERRIDE` trades and resumes monitoring. Unresolved breaches re-arm in the alert dispatcher so escalation resumes after a process restart.
  - Requirements: REQ-004, REQ-005, NFR-reliability
  - Files: `src/app.py`, `src/monitor/monitor.py`, `src/monitor/alerts.py`
  - Acceptance: kill the process mid-breach scenario, restart, verify alerts resume from the correct escalation level (or, simpler, restart escalation from level 0 — document the choice).
  - Dependencies: TASK-020

## Phase 9: Validation and error handling

- [ ] **TASK-022: Top-level error handlers**
  - Description: Wrap every bot handler and the monitor loop in a top-level try/except. Log with structlog (event name + context). User-facing reply on handler error: "Internal error, try again." Monitor crashes do not exit the process.
  - Requirements: NFR-reliability, design §8
  - Files: `src/bot/handlers.py`, `src/monitor/monitor.py`
  - Acceptance: injected exceptions are logged and the system keeps running; both loops survive an induced crash.
  - Dependencies: TASK-016, TASK-013

- [ ] **TASK-023: Race-safe trade transitions**
  - Description: The breach `INSERT` is gated by `WHERE status IN ('OPEN','OPEN_OVERRIDE')`; the `/closed` `UPDATE` is gated by the current status. Both inside transactions. A tick arriving during a close commit cannot create a breach for an already-closed trade.
  - Requirements: REQ-004, design §6 race section
  - Files: `src/db/repo.py`, `src/monitor/monitor.py`
  - Acceptance: targeted concurrency test interleaves a close and a breach-creating tick; only one wins, and the state is consistent.
  - Dependencies: TASK-005, TASK-013

## Phase 10: Testing

- [ ] **TASK-024: End-to-end test harness**
  - Description: Build `tests/e2e/` with a fake Telegram client and a scripted tick feeder. Implement scenarios:
    - Happy path: open → price moves favorably → `/closed` at profit.
    - Breach → `/closed`.
    - Breach → `/justify` → second breach (re-arming).
    - Breach → no response → escalation cadence verified.
    - WS disconnect during breach → reconnect → alerts continue.
    - WS down 15s with open trade → one alert at ~10s, one recovery on reconnect (REQ-009).
    - WS down 8s with open trade → no alert (debounced).
    - WS down 70s with no open trades → one alert at ~60s, one recovery.
    - Coverage gap > 60s → re-evaluation produces a breach on first post-reconnect tick if applicable.
    - Daily heartbeat fires at configured local time when healthy; suppressed when unhealthy.
    - Restart with an open trade and an unresolved breach.
  - Requirements: all
  - Files: `tests/e2e/*`
  - Acceptance: all scenarios pass in CI.
  - Dependencies: TASK-021

- [ ] **TASK-025: CI configuration**
  - Description: GitHub Actions workflow runs `ruff`, `black --check`, `mypy --strict`, `pytest` with coverage. Coverage gate ≥ 85% on `src/rules/` and `src/monitor/`.
  - Requirements: NFR-maintainability
  - Files: `.github/workflows/ci.yml`
  - Acceptance: PR cannot merge with failing checks.
  - Dependencies: TASK-024

## Phase 11: Documentation

- [ ] **TASK-026: README and runbook**
  - Description: Write `README.md` (what it is, setup, config, running, common commands) and `RUNBOOK.md` covering: WS down, Redis unavailable, Redis persistence failure, restart, restore from Redis AOF/RDB backup, rotating the bot token, AND process-level failure mitigations per REQ-009 (`docker compose` with `restart: always`, optional systemd supervision of the Compose stack, optional external uptime monitor consuming the heartbeat file, what to do if the daily heartbeat message stops arriving).
  - Requirements: NFR-maintainability, REQ-009
  - Files: `README.md`, `RUNBOOK.md`
  - Acceptance: a fresh reader can install, configure, and run within 30 minutes; runbook explicitly answers "what do I do if I stopped receiving the daily heartbeat?"
  - Dependencies: TASK-020

- [ ] **TASK-027: Deployment artifacts**
  - Description: Create `Dockerfile` (multi-stage, slim runtime) and `docker-compose.yml` as the primary runtime. Compose must define `bot` and `redis` services, set `REDIS_URL=redis://redis:6379/0` for the bot, configure Redis with AOF persistence, mount `${REDIS_DATA_DIR:-./data/redis}:/data`, use `restart: always` for both services, and mount the heartbeat-file path when configured. Provide a sample optional `systemd` unit with `Restart=always` and `RestartSec=5` for supervising the Compose stack, not replacing Compose.
  - Requirements: REQ-011, NFR-maintainability, REQ-009 (process-level failure mitigation)
  - Files: `Dockerfile`, `docker-compose.yml`, `deploy/btc-discipline.service`, `.env.example`
  - Acceptance: `docker compose up -d` brings up Redis and the bot; `/health` reports Redis and WS OK; creating a trade, running `docker compose down`, then `docker compose up -d` preserves the trade because Redis data remains in the mounted host directory; killing either container causes auto-restart within 10s.
  - Dependencies: TASK-020

- [ ] **TASK-031: Intelligence module stub + convention check (REQ-010)**
  - Description: Create `src/intelligence/__init__.py` as an empty package with the docstring from design.md §13.4 declaring the v2 boundary and the read-only constraint. Add a CI convention check (a pytest test or a custom ruff rule) that verifies:
    - No code outside `src/intelligence/` calls `repo.insert_signal(...)` or writes `signals:*` Redis keys directly.
    - No code inside `src/intelligence/` writes to `trade:*`, `breach:*`, `alert:*`, `conversation:*`, or their Redis indexes (verified via AST scan of repo method calls and key builders).
    - The `signals:*` Redis namespace remains empty after every v1 test run.
  - Also add a brief "v2 extension guide" section to `README.md` linking to design.md §13.
  - Requirements: REQ-010
  - Files: `src/intelligence/__init__.py`, `tests/unit/test_intelligence_boundary.py`, `README.md`
  - Acceptance: convention test passes on a clean v1 codebase; deliberately adding `repo.insert_signal()` in `src/monitor/monitor.py` causes the test to fail; `/signals` returns the documented stub message.
  - Dependencies: TASK-005, TASK-016, TASK-026

## Phase 12: Final verification

- [ ] **TASK-028: 24-hour real-feed smoke test**
  - Description: Run for 24h against the live Binance WS feed with one paper trade. Verify reconnect behavior, alert cadence, and absence of false breaches.
  - Requirements: REQ-004, REQ-005
  - Files: none (operational)
  - Acceptance: no false breaches; reconnect logs match any observed network blips; `/health` and `/stats` produce sane output throughout.
  - Dependencies: TASK-027

---

# Final Verification Checklist

- [ ] All requirements REQ-001 through REQ-010 are implemented and referenced in test docstrings
- [ ] All tests pass
- [ ] No `mypy --strict` errors
- [ ] No `ruff` / `black` errors
- [ ] Redis datastore migrations are safe (idempotent, forward-only, version-tracked)
- [ ] Error states are handled (form validation, WS disconnect, Redis error, Telegram send failure)
- [ ] Loading states are handled (N/A for v1 — commands sub-second)
- [ ] Empty states are handled (`/open`, `/stats`, `/streak` with no data)
- [ ] Security checks are in place (chat ID whitelist, Redis private network/default non-public exposure, safe key builders, secrets not logged)
- [ ] Race conditions handled (breach vs. close)
- [ ] Restart resilience verified (open trades resume monitoring, unresolved breaches resume escalation)
- [ ] Monitor health alerts verified end-to-end (tiered timing, recovery message, flapping detection, daily heartbeat)
- [ ] Process-level failure mitigations in place (`docker compose` services use `restart: always`, Redis data is host-mounted with AOF persistence, heartbeat file written, runbook documents the dead man's switch)
- [ ] AI-agent extension seams in place: event bus publishes the 7 v1 events; `RuleContext` is threaded through every rule and ignored in v1; `signals:*` Redis namespace exists by contract and is empty; `/signals` returns the stub message; `src/intelligence/` is an empty package; convention check enforces the read-only boundary
- [ ] Documentation (README, RUNBOOK, design §13) is updated
- [ ] Docker Compose runtime verified (`bot` + `redis` services, Redis AOF enabled, host-mounted Redis data persists across container recreation)
- [ ] 24h smoke test on real Binance feed passes
